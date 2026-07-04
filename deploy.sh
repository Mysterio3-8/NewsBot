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

echo "==> Готово."
