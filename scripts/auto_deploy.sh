#!/usr/bin/env bash
# Тик автодеплоя: раз в N минут смотрит, изменился ли код на GitHub, и только тогда
# зовёт полный deploy_from_github.sh.
#
# Зачем проверка изменений, а не просто «деплой по таймеру». deploy_from_github.sh
# ПЕРЕЗАПУСКАЕТ сервисы, а каждый рестарт news-rewriter немедленно запускает цикл
# публикации (AUTOSTART_SERVICE). Слепой прогон раз в 15 минут означал бы сотню
# рестартов в сутки — ровно та ситуация, которая 2026-07-05 маскировала гонки и
# помогла посту выйти 28 раз подряд. Ничего не изменилось — тик молча выходит.
#
# Зачем он вообще нужен, если есть GitHub Actions: это ВТОРОЙ, независимый путь.
# Actions ходят на сервер по ssh с ключом — он протухает, ssh падал уже дважды
# (2026-08-11, 2026-08-14). Здесь наоборот: сервер сам ходит наружу по HTTPS,
# и для этого не нужны ни ключи, ни входящие соединения.
set -euo pipefail

STATE_FILE=/root/.auto_deploy_state
SCRIPT_URL=https://raw.githubusercontent.com/Mysterio3-8/NewsBot/master/scripts/deploy_from_github.sh

# Репозиторий и его главная ветка. Порядок не важен — сверяем все три разом.
REPOS=(
    "Mysterio3-8/NewsBot master"
    "Mysterio3-8/softthmusic main"
    "Mysterio3-8/MinusZvyagaRepostFromYoutube master"
)

current=""
for entry in "${REPOS[@]}"; do
    # shellcheck disable=SC2086 — намеренно разбиваем строку на репозиторий и ветку
    set -- $entry
    sha="$(git ls-remote "https://github.com/$1" "refs/heads/$2" 2>/dev/null | awk '{print $1}')"
    if [ -z "$sha" ]; then
        # GitHub недоступен или сети нет. Молча выходим: пропущенный тик через 15
        # минут повторится сам, а деплой «вслепую» на неизвестном состоянии опаснее.
        echo "Автодеплой: не прочитать $1 — пропускаю тик"
        exit 0
    fi
    current="${current}$1:${sha}"$'\n'
done

if [ "$current" = "$(cat "$STATE_FILE" 2>/dev/null || true)" ]; then
    exit 0
fi

echo "Автодеплой: код на GitHub изменился — деплою"
curl -fsSL "$SCRIPT_URL" | bash

# Отметку ставим ТОЛЬКО после успешного деплоя (set -e прервёт скрипт раньше).
# Иначе упавший деплой считался бы применённым и следующий тик его не повторил бы.
printf '%s' "$current" > "$STATE_FILE"
echo "Автодеплой: готово"
