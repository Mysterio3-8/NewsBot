# AI News Rewriter

**Статус:** 🟡 dev (Этапы 0-5 реализованы: полный пайплайн TG/VK → LLM → изображения → публикация → UI → деплой; не проверено вручную с реальными Ollama/TG/VK — см. Checkpoint)

## Что это

Десктопное приложение (Python + PySide6), которое отслеживает заданные Telegram-каналы и VK-сообщества, отбирает новостные посты, переписывает их через локальную LLM (Ollama), готовит изображения с watermark и публикует по расписанию в Telegram и VK.

Полная спецификация: [SPEC.md](SPEC.md) — читать перед первой задачей в новой сессии.

## Архитектура (кратко)

```
Источники (TG/VK) → Фильтрация → LLM классификация → LLM rewrite
    → Изображения (пост/сток-API/локальная генерация) → Watermark
    → Очередь публикации → Telegram/VK
```

Подробности: разделы 4, 8–14 в [SPEC.md](SPEC.md).

## Toolchain / зависимости

- Python 3.12+ (см. грабли — локальный venv на 3.10.11)
- Ollama (локальная LLM, модель по умолчанию `qwen2.5:7b`)
- Telethon (MTProto, чтение чужих TG-каналов) + aiogram (публикация в TG)
- vk_api (чтение и публикация VK), APScheduler, Pillow, SQLAlchemy, PySide6, pytest/pytest-asyncio
- Опционально: локальный сервер Stable Diffusion (AUTOMATIC1111-совместимый API) для `LocalAIImageProvider`
- Секреты — только в `.env` (см. `.env.example`), никогда в `config.yaml` или коде

## Быстрые команды

```bash
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
python app/main.py            # десктоп UI
python app/main.py --headless # 24/7 сервер: check_interval → фильтр/LLM → очередь → автопубликация по расписанию
pytest tests/ -v               # 143 теста — вся бизнес-логика, часть UI (Qt headless)

# Docker (app + ollama)
docker compose up -d          # см. docker-compose.yml — llm.host в config.yaml должен быть http://ollama:11434
```

## Соглашения по коду

- Слои: `ui/` → только вызовы `core/*`, без бизнес-логики. `core/*` не знает о Qt. БД — только через `db/repository.py`.
- Каждый `ImageProvider` / `Publisher` — отдельный класс за общим интерфейсом.
- Промпты для LLM — только в `/prompts/*.txt`, не хардкодить в коде.
- Полный код-стайл — по правилам `.claude/rules/ecc/common` и `.claude/rules/ecc/python` (уже подключены в проект).

## Инварианты (нельзя нарушать)

- Никакого поиска новостей по всему интернету — только источники, добавленные пользователем.
- Никакого парсинга случайных изображений из поисковой выдачи — только пост/лицензионные сток-API/локальная генерация.
- Никакой обработки изображений для обхода антидубликат-алгоритмов (шум, микроповороты, обрезка на 1-2%).
- Никаких облачных LLM по умолчанию — только Ollama локально.
- Никакого обхода лимитов/банов соцсетей.

## Связи с другими модулями

Монолитное приложение с чёткими внутренними границами (раздел 18 SPEC.md):
`app/factories.py` — единая сборка fetcher/publisher из `.env` (используется и UI, и headless) → `app/core/check_cycle.py` (один цикл проверки всех источников) → `app/core/pipeline.py` (обработка одного поста: dedup → фильтры → LLM → скоринг → rewrite) → `app/headless_service.py` (APScheduler: check job на `check_interval_minutes` + publish job на `publishing.schedule`).

## Что осталось сделать

См. раздел 21 SPEC.md (Roadmap). **Этапы 0-5 реализованы полностью** (весь код + 143 автотеста). Не сделано / известные упрощения:
- Ручная проверка с реальными Ollama/Telegram/VK/сток-API не проводилась (нет доступа к реальным токенам в этой сессии) — см. чеклист первого запуска ниже.
- Telethon требует one-time интерактивный логин (номер телефона + код подтверждения) — не автоматизируется.
- UI-вкладки «Изображения» и «Планировщик» — информационные плейсхолдеры (сами провайдеры/scheduler реализованы и протестированы в `core/`, но не имеют отдельного UI-редактора; провайдеры настраиваются через `.env` + `config.yaml`, расписание автопубликации — только в headless-режиме).
- Автопубликация по расписанию (`headless_service.py`) сейчас публикует только в Telegram; VK — только вручную (нет `vk_queue_service`, аналогичного `queue_service.py`).
- Для VK-источников поле «ссылка» должно содержать числовой `group_id`, а не vanity-URL (резолвинг имени в id не реализован).

### Чеклист первого реального запуска
1. `ollama pull qwen2.5:7b` и убедиться, что `ollama serve` работает.
2. Скопировать `.env.example` → `.env`, заполнить `TG_BOT_TOKEN`, `TG_API_ID`/`TG_API_HASH` (my.telegram.org), `VK_USER_TOKEN`, `VK_GROUP_TOKEN`.
3. Первый запуск `python app/main.py --headless` вызовет интерактивный логин Telethon (номер телефона + код) — сделать это в интерактивном терминале один раз, сессия сохранится в `TG_SESSION_NAME`.
4. Положить логотип в `assets/logo.png` (нужен до применения watermark, иначе `WatermarkError`).
5. Добавить источники через UI (вкладка «Источники»), проверить публикацию вручную через «Публикация» перед включением автопубликации.

## Известные грабли

- Ollama должна быть запущена и модель скачана до старта — иначе явный статус `⛔ LLM недоступна`, не тихий fallback.
- Лимит caption в Telegram — 1024 символа, полный текст рерайта может не влезть в подпись к фото.
- VK API публикация фото — двухшаговая (`photos.getWallUploadServer` → `photos.saveWallPhoto`), не один вызов.
- `https://github.com/Mysterio3-8/NewsBot.git` — это не референс с готовым кодом, а целевой репозиторий, куда пользователь позже будет выгружать этот проект. Он пуст осознанно (ещё не запушен). Не путать с чужим кодом для заимствования архитектуры.
- Полный текст гуманизатора (`prompts/reference/humanizer_ru_full.md`) в system-промпт целиком не включён осознанно — на локальной 7B-модели такой объём инструкций на каждый вызов бьёт по скорости и качеству. В `prompts/system.txt` — сжатая выжимка самых частых паттернов. Если качество рерайта будет хромать — сначала пробовать точечно добавлять конкретные пункты из полного файла, не вставлять его целиком.
- Локальный `venv` создан на Python 3.10.11, хотя SPEC.md требует 3.12+ (на машине не было другой версии на момент Этапа 0). Код синтаксически совместим (используется `from __future__ import annotations`), но перед продакшен-деплоем стоит пересоздать venv на 3.12+.
- `assets/logo.png` пока не существует — нужен до применения watermark (`Watermarker` кидает явный `WatermarkError`, не тихий fallback).
- Ранее была найдена и исправлена ошибка самосравнения в дедупликации: `_check_local_filters` запрашивал `recent_content_hashes` уже ПОСЛЕ вставки текущего поста в БД, из-за чего каждый пост сравнивался сам с собой и считался дублем на 100%. Регрессионный тест: `tests/test_pipeline.py::test_process_fetched_post_rejects_near_duplicate_of_earlier_post`. Если снова трогаешь `app/core/pipeline.py::process_fetched_post` — сначала считать `recent_hashes`, потом `create_raw_post`.
- `QMessageBox.information/warning` в Qt-тестах блокируют выполнение модальным окном без клика — в каждом тесте, где код может дойти до показа диалога, обязательно патчить `app.ui.pages.<модуль>.QMessageBox.information`/`.warning` через `unittest.mock.patch`, иначе pytest зависает намертво (пришлось убивать процесс через `Stop-Process -Id` по конкретному PID, `taskkill /IM python.exe` глобально запрещён политикой безопасности).
- VK-источники: поле `url` в UI должно содержать числовой `group_id` (например `12345`), а не `https://vk.com/...` — `VKFetcher`/`check_cycle.py` делают `int(source.url)` напрямую.

## Checkpoint (2026-07-01)

- Сделано: полная реализация Этапов 0-5 по SPEC.md в рамках одной сессии (пользователь явно попросил "не MVP, а готовый продукт"):
  - **Этап 0**: `app/config` (типизированный `AppConfig`), `app/db` (модели + `Repository`), `app/ui` каркас, `app/main.py`, `app/logging_setup.py`.
  - **Этап 1 (MVP)**: `core/llm/{client,classifier,rewriter,headline_generator}.py`, `core/filtering/{rules,deduplication,scoring}.py`, `core/monitoring/telegram_fetcher.py` (Telethon), `core/publishing/telegram_publisher.py` (aiogram), `core/pipeline.py` (оркестрация new→queued), `core/publishing/queue_service.py` (ручная публикация).
  - **Этап 2**: `core/monitoring/vk_fetcher.py`, `core/publishing/vk_publisher.py` (двухшаговая загрузка фото), `core/scheduler.py` (fixed_slots/interval, лимит в сутки).
  - **Этап 3**: `core/images/providers/*` (source/unsplash/pexels/pixabay/local_ai), `core/images/watermark.py`, `core/images/image_pipeline.py` (приоритет провайдеров + watermark), `core/llm/image_query_generator.py`.
  - **Этап 4**: реальная связка UI↔core — Источники (CRUD), Главная (live-счётчики), Публикация (очередь + кнопка), ИИ (редактор промптов + статус LLM), Настройки (вкладка «Фильтры» редактируется и сохраняется в `config.yaml` через новый `update_config_section()`), Логи (tail-просмотр). Вкладки «Изображения»/«Планировщик» — информационные плейсхолдеры (сама логика в core/ реализована и тестами покрыта).
  - **Этап 5**: `app/factories.py` (сборка сервисов из `.env`), `app/core/check_cycle.py` + `app/headless_service.py` (APScheduler: периодическая проверка + автопубликация), очистка логов старше 30 дней (`logging_setup.cleanup_old_logs`), `deploy/ai-news-rewriter.service` (systemd), `Dockerfile` + `docker-compose.yml` (app + ollama).
  - Итог: **143 автотеста, все зелёные** (`pytest tests/ -v`).
- Активно: —
- Следующий шаг: см. «Что осталось сделать» выше — ручная проверка с реальными Ollama/TG/VK credentials по чеклисту первого запуска. Если что-то не работает при реальном запуске — это первое, что нужно диагностировать (моки в тестах не гарантируют совместимость с реальными API на 100%, особенно Telethon first-login и формат ответов внешних сток-API).
- Блокеры: нет технических; требуется участие пользователя для первого интерактивного логина Telethon и заполнения `.env`.
