#!/bin/bash

set -e

INSTALL_DIR="/opt/xui-tg-bot"
SYSTEMD_UNIT="/etc/systemd/system/xui-tg-bot.service"

echo "⏳ Остановка и отключение systemd-сервиса..."
systemctl stop xui-tg-bot || true
systemctl disable xui-tg-bot || true

echo "⏳ Удаление systemd unit-файла..."
rm -f "$SYSTEMD_UNIT"

echo "⏳ Перезагрузка systemd daemon..."
systemctl daemon-reload

echo "⏳ Удаление файлов бота и настроек..."
rm -rf "$INSTALL_DIR"

echo "✅ Удалено: $INSTALL_DIR, $SYSTEMD_UNIT"
echo "⚠️ Если вы устанавливали дополнительные Python-зависимости только для этого бота — их можно удалить вручную через pip3."

echo
echo "🎉 Деинсталляция завершена!"
