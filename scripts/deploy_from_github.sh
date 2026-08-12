#!/usr/bin/env bash
# Деплой ВСЕХ ТРЁХ софтов прямо с GitHub. Запускать НА СЕРВЕРЕ от root.
#
# Зачем понадобился. Обычный путь — `deploy.sh` с машины владельца, он заливает файлы
# по ssh. 2026-08-11 сервер перестал пускать по SSH (обмен ключами проходит, а на
# аутентификации соединение виснет), и доставить код стало нечем. Репозитории при этом
# публичные, а исходящий HTTPS с сервера работает — значит сервер может забрать код сам.
#
# Скрипт повторяет ровно то, что делает каждый deploy.sh, плюс два шага, которые не
# покрыты ни одним из них и потому регулярно забываются:
#   * `python -m app.seed_channels` — настройки каналов Кино и Новостей живут в БД, и
#     без прогона код на проде новый, а поведение старое;
#   * config.yaml Музыки — он в .gitignore, поэтому в репозитории лежит его прод-копия
#     `deploy/config.prod.yaml`.
#
# Что НЕ трогается: .env, data/, logs/, cookies.txt — всё машинно-специфичное и
# стейтфул остаётся на сервере как есть.
set -euo pipefail

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch() {
    local repo="$1" branch="$2" dest="$3"
    echo "==> Скачиваю $repo ($branch)"
    curl -fsSL "https://codeload.github.com/$repo/tar.gz/refs/heads/$branch" -o "$WORK/src.tgz"
    mkdir -p "$dest"
    # --strip-components=1 срезает верхний каталог вида «NewsBot-master».
    tar -xzf "$WORK/src.tgz" -C "$dest" --strip-components=1
    rm -f "$WORK/src.tgz"
}

# ---------------------------------------------------------------- Новости и Кино
fetch "Mysterio3-8/NewsBot" "master" "$WORK/news"
echo "==> Новости: обновляю app/ и prompts/"
cp -r "$WORK/news/app" "$WORK/news/prompts" /opt/news-rewriter/

echo "==> Новости: проверка импортов ДО рестарта"
cd /opt/news-rewriter && venv/bin/python -c '
import app.control_bot
import app.headless_service
import app.core.pipeline
import app.core.check_cycle
' || { echo "!! Импорты сломаны — сервис НЕ перезапущен, старый код продолжает работать"; exit 1; }

echo "==> Новости: настройки каналов в БД (SEO, лимиты, источники)"
cd /opt/news-rewriter && venv/bin/python -m app.seed_channels

systemctl restart news-rewriter-bot.service

# ---------------------------------------------------------------------- Минусы
fetch "Mysterio3-8/MinusZvyagaRepostFromYoutube" "master" "$WORK/minus"
echo "==> Минусы: обновляю код и config.yaml"
# Список файлов повторяет deploy.sh: он ЗАХАРДКОЖЕН и там, новый модуль надо дописывать
# в обоих местах, иначе импорт на сервере упадёт.
for f in autopost.py config.py db.py vk.py youtube.py playlists.py video_edit.py \
         uniquify.py upload_token.py vk_token_pool.py seo.py config.yaml; do
    cp "$WORK/minus/$f" /root/yt-vk/
done
cp -r "$WORK/minus/image" /root/yt-vk/ 2>/dev/null || true

echo "==> Минусы: проверка импортов и конфига"
cd /root/yt-vk && venv/bin/python -c '
import autopost, playlists, config
cfg = config.load_config()
assert cfg.templates, "post.templates пуст"
' || { echo "!! Минусы: импорты сломаны — сервис НЕ перезапущен"; exit 1; }

systemctl restart yt-vk-autopost.service

# ---------------------------------------------------------------------- Музыка
fetch "Mysterio3-8/softthmusic" "main" "$WORK/music"
echo "==> Музыка: обновляю app/, assets/, deploy/ и config.yaml"
cp -r "$WORK/music/app" "$WORK/music/assets" "$WORK/music/deploy" /opt/yt-vk-publisher/
cp "$WORK/music/deploy/config.prod.yaml" /opt/yt-vk-publisher/config.yaml
# Файлы юнитов тоже обновляем: без этого правка расписания в репозитории на прод
# не доезжает никогда, и таймер живёт с настройками месячной давности.
for unit in tg-sc-publisher.service tg-sc-publisher.timer \
            tg-yt-playlists.service tg-yt-playlists.timer; do
    [ -f "/opt/yt-vk-publisher/deploy/$unit" ] && \
        cp "/opt/yt-vk-publisher/deploy/$unit" /etc/systemd/system/
done
systemctl daemon-reload

echo "==> Музыка: проверка импортов и конфига"
cd /opt/yt-vk-publisher && venv/bin/python -c '
import app.album_publisher, app.yt_playlists, app.sc_autofill, app.sc_discovery
from app.config import load_config
cfg = load_config()
print("треков/сутки:", cfg.soundcloud.max_posts_per_day,
      "| автопоиск:", cfg.soundcloud.discovery.enabled,
      "| сборников:", cfg.youtube_playlists.max_posts_per_day)
' || { echo "!! Музыка: импорты или конфиг сломаны — таймер не трогаем"; exit 1; }

# ⚠️ У Музыки ДВА таймера, а не один: треки и сборники — независимые потоки со своими
# юнитами. Раньше перезапускался только первый, из-за чего сборники могли месяцами
# стоять незамеченными: код обновляется, а поток, который их публикует, не работает.
for unit in tg-sc-publisher.timer tg-yt-playlists.timer; do
    systemctl enable --now "$unit" 2>/dev/null || echo "!! Юнит $unit не найден — проверь вручную"
    systemctl restart "$unit" 2>/dev/null || true
done

# ------------------------------------------------------------ Проверка cookies
# 🔴 YouTube отвечает «Sign in to confirm you're not a bot» на серверных IP. Без cookies
# падает КАЖДЫЙ трек сборника и КАЖДЫЙ фильм Кино, а внешне это выглядит как «софт
# молчит»: очередь полна, таймер тикает, публикаций нет (инцидент 2026-08-11/12).
# Файл машинно-специфичный, в git его нет — проверяем и говорим вслух.
echo
echo "==> Cookies YouTube:"
COOKIES="${YT_COOKIES_FILE:-/root/yt-vk/cookies.txt}"
if [ -f "$COOKIES" ]; then
    echo "    файл на месте: $COOKIES"
    for env_file in /opt/news-rewriter/.env /opt/yt-vk-publisher/.env; do
        if grep -q '^YT_COOKIES_FILE=' "$env_file" 2>/dev/null; then
            echo "    $env_file — прописан"
        else
            echo "    !! $env_file — НЕ прописан. Выполни:"
            echo "       echo 'YT_COOKIES_FILE=$COOKIES' >> $env_file"
        fi
    done
else
    echo "    !! Файла $COOKIES нет. Без него YouTube не отдаст ни фильмы, ни сборники."
fi

# ------------------------------------------------------------------------ Итог
echo
echo "==> Готово. Состояние:"
df -h / | tail -1
free -m | head -2
echo "--- юниты ---"
systemctl is-active news-rewriter-bot yt-vk-autopost.service \
    tg-sc-publisher.timer tg-yt-playlists.timer
echo "--- очередь сборников ---"
cd /opt/yt-vk-publisher && venv/bin/python app/yt_playlists_cli.py status 2>&1 | tail -2
