"""Выбор рабочего выхода прокси для YouTube.

Зачем. Барьер «Sign in to confirm you're not a bot» зависит от IP: живой перебор пяти
VPN-выходов 2026-08-14 показал, что четыре из пяти закрыты, а шведский отдаёт видео без
всяких куки. Выход был прибит к рабочему руками — и это мина: IP протухает за недели, а
чинить пришлось бы человеку, узнав о поломке по пустой стене.

Здесь этот выбор делается сам. Все выходы подняты каждый на своём SOCKS-порту
(`10811..10815` на VPS, `xray`), плюс общий `10808`. Порядок работы:

1. берём выход, который сработал прошлый раз (помним в БД) — он почти всегда и нужен;
2. он отказал — перебираем остальные и встаём на первый рабочий;
3. не работает ни один — возвращаем None, и yt-dlp идёт напрямую. Прямой путь тоже
   иногда проходит, а «совсем не пробовать» гарантированно оставит сутки без фильма.

Проверка выхода — дешёвый запрос метаданных к YouTube, а не пинг: пинг проходит и через
закрытый выход, он ничего не говорит о барьере.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("monitoring")

PROXY_PORTS_ENV = "YT_PROXY_PORTS"
"""Список портов через запятую. Пусто → ротации нет, работает обычный YT_PROXY."""

LAST_GOOD_PROXY_KEY = "yt_last_good_proxy"
"""Где помним удачный выход. В настройках, а не в файле: настройки уже переживают
перезапуск и деплой, а файл на диске — нет."""

PROBE_VIDEO_ID = "Ew0UNAtOtfY"
"""Ролик для проверки. Обычный публичный ролик источника: проверять надо ровно тем
запросом, который потом и пойдёт, иначе проверка соврёт."""


def proxy_candidates(last_good: str | None = None) -> list[str]:
    """Адреса прокси в порядке проверки: сначала удачный в прошлый раз."""
    raw = os.environ.get(PROXY_PORTS_ENV, "").strip()
    if not raw:
        return []
    ports = [part.strip() for part in raw.split(",") if part.strip()]
    urls = [f"socks5://127.0.0.1:{port}" for port in ports]
    if last_good in urls:
        urls.remove(last_good)
        urls.insert(0, last_good)
    return urls


def probe_proxy(proxy: str | None, *, video_id: str = PROBE_VIDEO_ID) -> bool:
    """Отдаёт ли YouTube через этот выход настоящие форматы.

    Именно форматы, а не «ответил ли сервер»: при барьере ответ приходит, но в нём одни
    раскадровки, и по коду ответа это неотличимо от рабочего случая."""
    import yt_dlp

    from app.core.video.video_source import METADATA_THROTTLE, ytdlp_options

    options = ytdlp_options(skip_download=True, **METADATA_THROTTLE)
    if proxy:
        options["proxy"] = proxy
    else:
        options.pop("proxy", None)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False, process=False
            )
    except Exception as error:  # noqa: BLE001 — граница сети, отказ это штатный ответ
        logger.info("Прокси %s не подошёл: %s", proxy or "прямой путь", str(error)[:80])
        return False
    formats = info.get("formats") or []
    playable = [item for item in formats if item.get("vcodec") not in (None, "none")]
    if not playable:
        logger.info("Прокси %s отдаёт только раскадровки", proxy or "прямой путь")
        return False
    return True


def pick_working_proxy(repo, *, probe=probe_proxy, exclude: str | None = None) -> str | None:
    """Найти рабочий выход и запомнить его. None — идём напрямую.

    `exclude` — выход, который только что подвёл на СКАЧИВАНИИ. Его пропускаем, даже если
    метаданные через него отдаются: CDN отвечает `403 Forbidden` отдельно от плеера, и
    выход, прошедший проверку, всё равно может не отдать сами данные (случилось 15.08).
    Без этого повтор упирался бы в тот же самый выход и был бы бесполезен.

    `probe` инжектируется, чтобы тесты не ходили в сеть."""
    last_good = repo.get_setting(LAST_GOOD_PROXY_KEY)
    candidates = [item for item in proxy_candidates(last_good) if item != exclude]
    if not candidates:
        fallback = os.environ.get("YT_PROXY", "").strip() or None
        return None if fallback == exclude else fallback

    for proxy in candidates:
        if probe(proxy):
            if proxy != last_good:
                repo.set_setting(LAST_GOOD_PROXY_KEY, proxy)
                logger.warning("Прокси переключён на %s", proxy)
            return proxy

    logger.error("Ни один прокси-выход не проходит барьер YouTube — идём напрямую")
    return None
