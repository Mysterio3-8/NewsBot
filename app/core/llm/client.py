"""Единый клиент локальной LLM через Ollama HTTP API (раздел 9 SPEC.md)."""
from __future__ import annotations

import logging
import re

import requests

from app.config.loader import LLMConfig
from app.paths import PROMPTS_DIR

logger = logging.getLogger("llm")

PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")


class LLMUnavailableError(Exception):
    """Ollama не запущена, модель не скачана, либо запрос не удался после retry."""


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def is_running(self) -> bool:
        try:
            response = requests.get(f"{self._config.host}/api/tags", timeout=5)
            return response.ok
        except requests.RequestException:
            return False

    def is_model_downloaded(self) -> bool:
        try:
            response = requests.get(f"{self._config.host}/api/tags", timeout=5)
            response.raise_for_status()
        except requests.RequestException:
            return False

        model_names = [m["name"] for m in response.json().get("models", [])]
        return self._config.model in model_names

    def load_prompt(self, name: str) -> str:
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
            raise LLMUnavailableError("Ollama недоступна (LLM недоступна)")

        payload = {
            "model": self._config.model,
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

        attempts = 1 + self._config.retries
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            logger.info("LLM запрос (попытка %d/%d): %s", attempt, attempts, user_prompt[:200])
            try:
                response = requests.post(
                    f"{self._config.host}/api/chat",
                    json=payload,
                    timeout=self._config.timeout_seconds,
                )
                response.raise_for_status()
                content = response.json()["message"]["content"]
                logger.info("LLM ответ: %s", content[:500])
                return content
            except (requests.RequestException, KeyError) as error:
                last_error = error
                logger.warning("LLM запрос не удался (попытка %d/%d): %s", attempt, attempts, error)

        raise LLMUnavailableError(f"LLM запрос не удался после {attempts} попыток") from last_error
