"""Единый клиент LLM: Ollama (локально) или облачные API (Gemini/Groq/OpenRouter) — раздел 9 SPEC.md."""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
import time
from pathlib import Path

import requests

from app.config.loader import LLMConfig
from app.core.llm import prompt_store
from app.paths import PROMPTS_DIR

logger = logging.getLogger("llm")

PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GROQ_API_BASE = "https://api.groq.com/openai/v1"
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
# Groq и OpenRouter — OpenAI-совместимые (одинаковый /chat/completions и /models),
# отличаются только базовым URL. Один код-путь на оба (см. _generate_openai_compatible).
OPENAI_COMPATIBLE_BASES = {"groq": GROQ_API_BASE, "openrouter": OPENROUTER_API_BASE}
LLM_PROXY_ENV_VAR = "LLM_PROXY_URL"
CLOUD_PROVIDERS = {"gemini", "groq", "openrouter"}
# Gemini геоблокирован для РФ и требует прокси (см. известные грабли в CLAUDE.md).
# Groq/OpenRouter работают из РФ напрямую — гонять их через прокси не нужно и вредно
# (лишняя точка отказа на нестабильном триальном прокси).
PROXIED_PROVIDERS = {"gemini"}
# Пайплайн делает несколько вызовов generate() подряд на один пост (классификация,
# рерайт, заголовок, image-query) — без паузы это упирается в лимиты бесплатного tier.
# Gemini free tier тут крайне тесный (20 запросов/день/модель). У Groq запросов много
# (1000-14400/день), но токенов в минуту (TPM) всего 6000-12000 — а один реальный вызов
# (system-промпт ~1400 токенов + шаблон + текст поста) уже даёт ~1700-2500 токенов,
# замерено эмпирически (classify_post = 1768 токенов). Больше 3 вызовов/мин не поместится.
# OpenRouter free: 20 запросов/мин (= 3с между вызовами) + жёсткий дневной потолок
# (50/день без пополнения, 1000/день после $10) — интервал держит минутный лимит.
MIN_REQUEST_INTERVAL_SECONDS = {"gemini": 4.5, "groq": 20.0, "openrouter": 3.5}
RETRY_BACKOFF_SECONDS = {"gemini": 15.0, "groq": 25.0, "openrouter": 10.0}
MAX_RETRY_AFTER_SECONDS = 60.0


# Дополнительные ключи того же провайдера ищутся как {api_key_env}_2, _3, ... — лимиты
# Groq считаются НА АККАУНТ, поэтому исчерпанный ключ не значит, что модель недоступна:
# переключаемся на следующий ключ и продолжаем работать основной (качественной) моделью,
# а не падаем на слабую fallback-модель (ТЗ 2026-07-28).
MAX_EXTRA_API_KEYS = 9


class LLMUnavailableError(Exception):
    """LLM недоступна (сервер/ключ не отвечает, модель не найдена, либо запрос не удался после retry)."""


class LLMRateLimitError(LLMUnavailableError):
    """Лимит/квота ключа исчерпаны (429) — есть смысл повторить запрос с другим ключом."""


def _is_rate_limit(error: Exception) -> bool:
    response = getattr(error, "response", None)
    return response is not None and getattr(response, "status_code", None) == 429


def _retry_after_seconds(error: Exception) -> float | None:
    """При 429 провайдеры сообщают точное время до сброса лимита — используем его
    вместо фиксированной паузы (Retry-After — стандартный HTTP-заголовок у Groq;
    retryDelay в теле ответа — формат Gemini)."""
    response = getattr(error, "response", None)
    if response is None:
        return None

    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass

    try:
        details = response.json().get("error", {}).get("details", [])
    except (ValueError, AttributeError):
        return None
    for detail in details:
        delay = detail.get("retryDelay")
        if delay and delay.endswith("s"):
            try:
                return min(float(delay[:-1]), MAX_RETRY_AFTER_SECONDS)
            except ValueError:
                continue
    return None


class LLMClient:
    def __init__(self, config: LLMConfig, repo=None) -> None:
        self._config = config
        self._last_request_at: float | None = None
        self._key_index = 0
        # repo нужен только для переопределений промптов из бота (prompt_store).
        # None → работаем на заводских файлах, как раньше.
        self._repo = repo

    def is_running(self) -> bool:
        if self._config.provider in CLOUD_PROVIDERS:
            return self._cloud_model_names() is not None
        try:
            response = requests.get(f"{self._config.host}/api/tags", timeout=5)
            return response.ok
        except requests.RequestException:
            return False

    def is_model_downloaded(self) -> bool:
        if self._config.provider in CLOUD_PROVIDERS:
            model_names = self._cloud_model_names()
            return model_names is not None and self._config.model in model_names
        try:
            response = requests.get(f"{self._config.host}/api/tags", timeout=5)
            response.raise_for_status()
        except requests.RequestException:
            return False

        model_names = [m["name"] for m in response.json().get("models", [])]
        return self._config.model in model_names

    def load_prompt(self, name: str) -> str:
        """Текст шаблона: сначала правка владельца из бота (БД), иначе заводской файл.
        Так изменённый промпт переживает деплой — deploy.sh синкает prompts/ с диска."""
        override = prompt_store.get_override(self._repo, name)
        if override:
            return override
        prompt_path = PROMPTS_DIR / f"{name}.txt"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Промпт не найден: {prompt_path}")
        return prompt_path.read_text(encoding="utf-8")

    def render(self, template: str, **placeholders: str) -> str:
        def substitute(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in placeholders:
                raise KeyError(f"Плейсхолдер {{{{{key}}}}} не передан для рендера промпта")
            return placeholders[key]

        return PLACEHOLDER_PATTERN.sub(substitute, template)

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if not self.is_running():
            raise LLMUnavailableError("LLM недоступна")

        models = self._models_to_try()
        last_error: Exception | None = None
        for model in models:
            # Лимит Groq считается НА КЛЮЧ (аккаунт): исчерпан ключ — не значит, что
            # модель недоступна. Пробуем ту же (качественную) модель другими ключами и
            # только потом опускаемся на резервную модель послабее.
            for _ in range(max(len(self._api_keys()), 1)):
                try:
                    return self._generate_with_model(system_prompt, user_prompt, model)
                except LLMRateLimitError as error:
                    last_error = error
                    if not self._rotate_api_key():
                        break
                    logger.warning(
                        "Лимит ключа исчерпан (model=%s), переключаюсь на следующий ключ", model
                    )
                except LLMUnavailableError as error:
                    last_error = error
                    if len(models) > 1:
                        logger.warning("Модель %s недоступна, пробую следующую: %s", model, error)
                    break

        raise LLMUnavailableError("Все модели недоступны") from last_error

    def _models_to_try(self) -> list[str]:
        """Основная модель + резервные (fallback_models), без дублей, порядок сохраняется."""
        ordered: list[str] = []
        for model in [self._config.model, *self._config.fallback_models]:
            if model and model not in ordered:
                ordered.append(model)
        return ordered

    def _generate_with_model(self, system_prompt: str, user_prompt: str, model: str) -> str:
        attempts = 1 + self._config.retries
        last_error: Exception | None = None
        backoff_seconds = RETRY_BACKOFF_SECONDS.get(self._config.provider, 0.0)
        for attempt in range(1, attempts + 1):
            if attempt > 1 and backoff_seconds > 0:
                time.sleep(backoff_seconds)
            logger.info(
                "LLM запрос model=%s (попытка %d/%d): %s", model, attempt, attempts, user_prompt[:200]
            )
            try:
                content = self._dispatch_generate(system_prompt, user_prompt, model)
                logger.info("LLM ответ: %s", content[:500])
                return content
            except (requests.RequestException, KeyError, IndexError) as error:
                last_error = error
                logger.warning(
                    "LLM запрос не удался model=%s (попытка %d/%d): %s",
                    model, attempt, attempts, error,
                )
                backoff_seconds = _retry_after_seconds(error) or backoff_seconds

        if last_error is not None and _is_rate_limit(last_error):
            raise LLMRateLimitError(
                f"лимит ключа исчерпан на модели {model} (429)"
            ) from last_error
        raise LLMUnavailableError(
            f"модель {model} недоступна после {attempts} попыток"
        ) from last_error

    def _dispatch_generate(self, system_prompt: str, user_prompt: str, model: str) -> str:
        if self._config.provider == "gemini":
            return self._generate_gemini(system_prompt, user_prompt, model)
        if self._config.provider in OPENAI_COMPATIBLE_BASES:
            return self._generate_openai_compatible(system_prompt, user_prompt, model)
        return self._generate_ollama(system_prompt, user_prompt, model)

    def _generate_ollama(self, system_prompt: str, user_prompt: str, model: str) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {
                "temperature": self._config.temperature,
                "top_p": self._config.top_p,
            },
        }
        response = requests.post(
            f"{self._config.host}/api/chat",
            json=payload,
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    def _generate_gemini(self, system_prompt: str, user_prompt: str, model: str) -> str:
        self._throttle()
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self._config.temperature,
                "topP": self._config.top_p,
            },
        }
        response = requests.post(
            f"{GEMINI_API_BASE}/models/{model}:generateContent",
            params={"key": self._cloud_api_key()},
            json=payload,
            timeout=self._config.timeout_seconds,
            proxies=self._cloud_proxies(),
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]

    def _generate_openai_compatible(self, system_prompt: str, user_prompt: str, model: str) -> str:
        """Единый путь для Groq и OpenRouter — оба принимают OpenAI-формат
        /chat/completions и отвечают одинаковой структурой choices[0].message.content."""
        self._throttle()
        base = OPENAI_COMPATIBLE_BASES[self._config.provider]
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._config.temperature,
            "top_p": self._config.top_p,
        }
        response = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {self._cloud_api_key()}"},
            json=payload,
            timeout=self._config.timeout_seconds,
            proxies=self._cloud_proxies(),
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def generate_vision(self, prompt: str, image_path: Path) -> str:
        """Vision-запрос (анализ изображения) — только для OpenAI-совместимых
        провайдеров (Groq/OpenRouter, см. OPENAI_COMPATIBLE_BASES) и только если
        задан config.llm.vision_model. Используется detect_foreign_watermark
        (app/core/images/watermark_detector.py); при недоступности вызывающий код
        считает, что проверка не удалась, и не блокирует публикацию (fail-open)."""
        if self._config.provider not in OPENAI_COMPATIBLE_BASES:
            raise LLMUnavailableError(f"Vision не поддерживается для provider={self._config.provider}")
        if not self._config.vision_model:
            raise LLMUnavailableError("config.llm.vision_model не задан")

        self._throttle()
        image_base64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        base = OPENAI_COMPATIBLE_BASES[self._config.provider]
        payload = {
            "model": self._config.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                        },
                    ],
                }
            ],
            "temperature": 0.1,
        }
        try:
            response = requests.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {self._cloud_api_key()}"},
                json=payload,
                timeout=self._config.timeout_seconds,
                proxies=self._cloud_proxies(),
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, IndexError) as error:
            raise LLMUnavailableError(f"Vision-запрос не удался: {error}") from error

    def _throttle(self) -> None:
        interval = MIN_REQUEST_INTERVAL_SECONDS.get(self._config.provider, 0.0)
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _api_keys(self) -> list[str]:
        """Все ключи провайдера: основной {api_key_env} + дополнительные {api_key_env}_2..N.
        Несколько аккаунтов Groq дают суммарный дневной лимит больше одного."""
        if not self._config.api_key_env:
            return []
        keys = [os.environ.get(self._config.api_key_env)]
        keys += [
            os.environ.get(f"{self._config.api_key_env}_{i}")
            for i in range(2, MAX_EXTRA_API_KEYS + 2)
        ]
        return [key for key in keys if key]

    def _cloud_api_key(self) -> str | None:
        keys = self._api_keys()
        if not keys:
            return None
        return keys[self._key_index % len(keys)]

    def _rotate_api_key(self) -> bool:
        """Переключиться на следующий ключ. False — ключ всего один, переключать некуда."""
        keys = self._api_keys()
        if len(keys) < 2:
            return False
        self._key_index = (self._key_index + 1) % len(keys)
        return True

    def _cloud_proxies(self) -> dict[str, str] | None:
        """Некоторые облачные LLM (Gemini) недоступны из РФ напрямую — LLM_PROXY_URL
        (SOCKS5/HTTP) в .env маршрутизирует их запросы через прокси. Провайдеры вне
        PROXIED_PROVIDERS (Groq) идут напрямую — прокси им не нужен и не добавляется."""
        if self._config.provider not in PROXIED_PROVIDERS:
            return None
        proxy_url = os.environ.get(LLM_PROXY_ENV_VAR)
        if not proxy_url:
            return None
        return {"https": proxy_url, "http": proxy_url}

    def _cloud_model_names(self) -> list[str] | None:
        api_key = self._cloud_api_key()
        if api_key is None:
            return None
        try:
            if self._config.provider == "gemini":
                response = requests.get(
                    f"{GEMINI_API_BASE}/models",
                    params={"key": api_key},
                    timeout=5,
                    proxies=self._cloud_proxies(),
                )
                response.raise_for_status()
                return [m["name"].removeprefix("models/") for m in response.json().get("models", [])]

            response = requests.get(
                f"{OPENAI_COMPATIBLE_BASES[self._config.provider]}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5,
                proxies=self._cloud_proxies(),
            )
            response.raise_for_status()
            return [m["id"] for m in response.json().get("data", [])]
        except requests.RequestException:
            return None
