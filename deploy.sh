#!/usr/bin/env bash
# Автодеплой на прод-VPS (news-rewriter-vps, 38.244.213.132).
# Синхронизирует app/ и prompts/ (не requirements.txt/.env — те у сервера
# свои, headless-специфичные, ручное редактирование), проверяет импорты
# ПЕРЕД рестартом (чтобы не уронить прод сломанным кодом), затем
# перезапускает systemd-сервис.
set -euo pipefail

HOST="news-rewriter-vps"
REMOTE_DIR="/opt/news-rewriter"
SERVICE="news-rewriter-bot.service"

# ⚠️ ssh из Git Bash не работает, когда имя пользователя Windows написано КИРИЛЛИЦЕЙ.
# msys-сборка OpenSSH не находит домашний каталог: сначала это выглядело как «Could not
# resolve hostname» (не читается ~/.ssh/config), потом — «Host key verification failed»
# (не читается known_hosts), а после явных -F/-o упёрлось в ключ: путь к нему msys
# отдаёт в своей кодировке (`/c/Users/\310\353\374\377/.ssh/id_ed25519`), и файла по
# такому пути нет. Хук post-commit крутится именно в этой оболочке — отсюда и «коммит =
# деплой» не работал ВООБЩЕ (2026-08-11 … 2026-08-13).
#
# Решение: звать ВИНДОВЫЙ ssh.exe, если он есть. Он читает те же ~/.ssh/config и
# known_hosts, но домашний каталог берёт из Windows и кириллицу переваривает.
# Git Bash остаётся запасным путём — на машине без OpenSSH ничего не ломается.
WIN_SSH="/c/Windows/System32/OpenSSH/ssh.exe"
if [ -x "$WIN_SSH" ]; then
  SSH=("$WIN_SSH")
elif [ -f "$HOME/.ssh/config" ]; then
  # known_hosts указываем тем же явным путём: иначе ssh ищет его по ненайденному
  # домашнему каталогу, не находит запись сервера и падает «Host key verification failed».
  SSH=(ssh -F "$HOME/.ssh/config" -o "UserKnownHostsFile=$HOME/.ssh/known_hosts")
else
  SSH=(ssh)
fi
ssh() { command "${SSH[@]}" "$@"; }

echo "==> Синхронизация app/ и prompts/ на $HOST..."
tar -czf - --exclude='__pycache__' --exclude='*.pyc' app prompts \
  | ssh "$HOST" "tar -xzf - -C $REMOTE_DIR"

echo "==> Проверка импортов на сервере..."
ssh "$HOST" "cd $REMOTE_DIR && venv/bin/python -c '
import app.control_bot
import app.headless_service
import app.core.pipeline
import app.core.check_cycle
' " || { echo "!! Импорты сломаны — сервис НЕ перезапущен, старый код продолжает работать."; exit 1; }

echo "==> Импорты чистые. Перезапуск $SERVICE..."
ssh "$HOST" "systemctl restart $SERVICE && sleep 2 && systemctl is-active $SERVICE"

# Требование владельца 2026-08-04: «после каждого нового обновления надо делать один
# тестовый пост». Проверяется именно путь С МЕДИА — поломка, из-за которой месяц шли
# посты голым текстом, была зелёной по всем юнит-тестам. Пост публикуется и тут же
# удаляется. Занятый пул токенов деплой НЕ валит (это не регрессия), публикация без
# вложения — валит.
# Дым-тест ЗАНИМАЕТ боевой слот пула (их всего ~36 в сутки на 18 публикаций), поэтому
# на каждый деплой он больше не гоняется — иначе сам отбирает контент у каналов.
# Запуск по требованию: SMOKE=1 bash deploy.sh
if [ "${SMOKE:-0}" != "1" ]; then
  echo "==> Дым-тест пропущен (SMOKE=1 чтобы прогнать). Готово."
  exit 0
fi
echo "==> Дым-тест публикации с медиа..." 
set +e
ssh "$HOST" "cd $REMOTE_DIR && venv/bin/python -m app.smoke_media_publish"
SMOKE_CODE=$?
set -e
# 255 — обрыв самого ssh, а не вердикт теста. Раньше это печаталось как «публикация
# сломана» и пугало зря: сервер моргал, а медиа было ни при чём.
if [ "$SMOKE_CODE" -eq 255 ]; then
  echo "!! Связь с сервером оборвалась — дым-тест НЕ выполнен. Код задеплоен."
  echo "   Проверить вручную: ssh $HOST 'cd $REMOTE_DIR && venv/bin/python -m app.smoke_media_publish'"
elif [ "$SMOKE_CODE" -ne 0 ]; then
  echo "!! ДЫМ-ТЕСТ ПРОВАЛЕН: публикация с медиа сломана. Код задеплоен — чинить сейчас."
  exit 1
fi
echo "==> Готово."
