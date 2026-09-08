"""Провал одной сети не роняет в `failed` пост, уже вышедший в другой.

🔴 Живой случай 07–08.09: шесть постов Новостей (1982–1990) ушли в Telegram, VK не
удался — занятый пул токенов и неудачная загрузка фото, — и все шесть легли в `failed`.
Догон (`_requeue_until_networks_done`) поднимает обратно только посты со статусом
`published`, поэтому в сообщество они не вышли уже никогда: за сутки VK получил 1–2
поста при лимите 5, а сторож тишины писал «Новости молчат 22 ч».

Дыра открылась именно с появлением догона: он СПЕЦИАЛЬНО держит вышедший в TG пост в
`queued`, а проверка «уже опубликован» смотрела на статус и такой пост считала
неопубликованным.
"""

from __future__ import annotations

import datetime

from app.core.publishing.queue_service import _already_published as tg_already_published
from app.core.publishing.vk_queue_service import _already_published as vk_already_published
from app.db.repository import Repository, init_db, make_engine


def _repo(tmp_path, name: str) -> Repository:
    engine = make_engine(tmp_path / name)
    init_db(engine)
    return Repository(engine)


def _post(repo: Repository) -> int:
    source = repo.create_source(type="telegram", name="s", url="u")
    raw = repo.create_raw_post(
        source_id=source.id, external_id=f"e{datetime.datetime.utcnow().timestamp()}",
        raw_text="текст", media=None,
    )
    return repo.create_processed_post(raw_post_id=raw.id, rewritten_text="текст", score=80.0).id


def test_post_already_in_telegram_is_not_marked_failed(tmp_path):
    """Пост в `queued` с отметкой TG — это догон ждёт VK, а не свежий неудачник."""
    repo = _repo(tmp_path, "tg.db")
    post_id = _post(repo)
    repo.mark_published(post_id, network="tg", published_at=datetime.datetime.utcnow())
    repo.update_processed_post_status(post_id, "queued")

    assert vk_already_published(repo, post_id) is True
    assert tg_already_published(repo, post_id) is True


def test_post_that_never_went_out_is_still_failable(tmp_path):
    """Обычный провал терять нельзя: пост без единой отметки обязан уйти в `failed`,
    иначе очередь копила бы вечных кандидатов и жгла бы LLM на них по кругу."""
    repo = _repo(tmp_path, "fresh.db")
    post_id = _post(repo)

    assert vk_already_published(repo, post_id) is False
    assert tg_already_published(repo, post_id) is False


def test_published_status_still_counts(tmp_path):
    """Прежнее поведение сохраняем: статус `published` — по-прежнему «уже вышел»."""
    repo = _repo(tmp_path, "published.db")
    post_id = _post(repo)
    repo.mark_published(post_id, network="vk", published_at=datetime.datetime.utcnow(), vk_post_id=7)

    assert vk_already_published(repo, post_id) is True
