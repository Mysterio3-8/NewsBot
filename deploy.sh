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

# Требование владельца 2026-08-04: «после каждого нового обновления надо делать один
# тестовый пост». Проверяется именно путь С МЕДИА — поломка, из-за которой месяц шли
# посты голым текстом, была зелёной по всем юнит-тестам. Пост публикуется и тут же
# удаляется. Занятый пул токенов деплой НЕ валит (это не регрессия), публикация без
# вложения — валит.
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
