"""Сид канала «Новости» (ТЗ 2026-07-26): включить VK+TG, оставить один источник
«прямой эфир», выставить консервативный антибан. Идемпотентно."""
import json

from app.core.channel_settings import ChannelSettings
from app.db.repository import DEFAULT_CHANNEL_NAME, Repository, init_db, make_engine
from app.seed_channels import NEWS_SOURCE_URL, seed_news


def _news_channel(repo: Repository):
    return next(c for c in repo.list_channels() if c.name == DEFAULT_CHANNEL_NAME)


def _seed_default_channel(repo: Repository, *, enabled: bool = True):
    """На проде канал «Новости» уже существует (создан миграцией _ensure_default_channel).
    В пустой тест-БД его нет — заводим сами, чтобы моделировать прод."""
    return repo.create_channel(name=DEFAULT_CHANNEL_NAME, enabled=enabled)


def test_seed_news_enables_channel_and_narrows_to_single_source(tmp_path):
    engine = make_engine(tmp_path / "news.db")
    init_db(engine)
    repo = Repository(engine)
    channel = _seed_default_channel(repo, enabled=False)
    repo.create_source(type="tg", name="прямой эфир", url=NEWS_SOURCE_URL, channel_id=channel.id)
    repo.create_source(type="tg", name="лишний", url="https://t.me/toporlive", channel_id=channel.id)
    repo.create_source(type="vk", name="лишний-vk", url="-191269950", channel_id=channel.id)

    seed_news(repo)

    channel = _news_channel(repo)
    assert channel.enabled is True
    enabled_urls = {s.url for s in repo.list_sources(channel_id=channel.id) if s.enabled}
    assert enabled_urls == {NEWS_SOURCE_URL}


def test_seed_news_sets_conservative_antiban(tmp_path):
    engine = make_engine(tmp_path / "antiban.db")
    init_db(engine)
    repo = Repository(engine)
    _seed_default_channel(repo)

    seed_news(repo)

    settings = ChannelSettings.from_json(_news_channel(repo).settings_json)
    assert settings.max_posts_per_day <= 50
    assert settings.min_interval_minutes is not None
    assert settings.max_interval_minutes is not None
    assert settings.min_interval_minutes < settings.max_interval_minutes
    # Ночная пауза снята осознанно (ТЗ 2026-08-03) — вместо неё антибан держат объём,
    # широкий интервал и пул личных токенов, размазывающий загрузки по аккаунтам.
    assert settings.quiet_start_hour == settings.quiet_end_hour
    # Пул может состоять из ОДНОГО аккаунта: 2026-08-14 второй оказался забанен и был
    # убран. Требовать двух нельзя — тогда тест толкал бы обратно к тому, чтобы держать
    # в ротации заведомо мёртвый токен. Важно, что пул не пуст: пустой означает возврат
    # к одному захардкоженному токену мимо балансера и зазора.
    assert settings.vk_upload_token_envs
    # Интервал должен физически растягивать дневной лимит на все сутки.
    assert settings.max_posts_per_day * settings.min_interval_minutes >= 20 * 60


def test_seed_news_removes_telegram_footer_link(tmp_path):
    """ТЗ владельца 2026-08-12: «убери ссылку, которая ведёт на телеграм».

    Проверяем именно ПЕРЕЗАПИСЬ уже засеянного значения: merge_channel_settings только
    дописывает ключи, поэтому убрать настройку можно лишь явным None — просто выкинуть
    её из сида недостаточно, в прод-БД она осталась бы жить."""
    engine = make_engine(tmp_path / "footer.db")
    init_db(engine)
    repo = Repository(engine)
    channel = _seed_default_channel(repo)
    repo.update_channel(
        channel.id,
        settings_json=json.dumps({"tg_footer_url": "https://t.me/NewsThreeWord",
                                  "tg_footer_signature": "🔢 Новости в трёх словах"}),
    )

    seed_news(repo)

    settings = ChannelSettings.from_json(_news_channel(repo).settings_json)
    assert settings.tg_footer_url is None
    assert settings.tg_footer_signature is None


def test_cinema_plan_matches_the_owners_order():
    """ТЗ владельца 2026-08-12: «1 фильм — 2 клипа и 4 поста», фильм и клипы снова
    записями на стене.

    Раздел «Видео» пробовали с 2026-08-10 и откатили: ролик, ушедший в каталог
    сообщества, в ленте не виден вообще, и канал выглядел полупустым («в кино мало
    публикаций, нет фильмов и клипов»)."""
    from app.seed_channels import DAILY_PLAN

    assert DAILY_PLAN["daily_video_count"] == 1
    assert DAILY_PLAN["daily_clip_count"] == 2
    assert DAILY_PLAN["max_posts_per_day"] == 4
    assert DAILY_PLAN["video_as_post"] is True
    # 1440 / 4 поста = 360 мин — потолок среднего интервала, иначе четвёртый пост
    # не влезет в сутки.
    average = (DAILY_PLAN["min_interval_minutes"] + DAILY_PLAN["max_interval_minutes"]) / 2
    assert average <= 360


def test_seed_news_enables_simple_media(tmp_path):
    engine = make_engine(tmp_path / "media.db")
    init_db(engine)
    repo = Repository(engine)
    _seed_default_channel(repo)

    seed_news(repo)

    assert ChannelSettings.from_json(_news_channel(repo).settings_json).simple_media is True


def test_seed_news_is_idempotent(tmp_path):
    engine = make_engine(tmp_path / "idem.db")
    init_db(engine)
    repo = Repository(engine)
    _seed_default_channel(repo)

    seed_news(repo)
    seed_news(repo)

    channel = _news_channel(repo)
    assert channel.enabled is True
    # settings_json не раздувается дублями ключей — merge обновляет на месте
    assert ChannelSettings.from_json(channel.settings_json).max_posts_per_day == 5
