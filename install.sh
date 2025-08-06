#!/bin/bash

set -e

INSTALL_DIR="/opt/xui-tg-bot"
REPO_URL="https://github.com/maxgalzer/xray-traffic-bot"

echo "⏳ Обновление системы и установка зависимостей..."

apt update && apt upgrade -y

# Установка python3, pip, sqlite3, git
apt install -y python3 python3-pip sqlite3 git

# Проверка наличия systemd
if ! pidof systemd > /dev/null; then
  echo "❌ Systemd не найден! Скрипт требует systemd для автозапуска."
  exit 1
fi

echo "✅ Все зависимости установлены!"

# Создание рабочей папки
if [ ! -d "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR"
    echo "✅ Папка для бота создана: $INSTALL_DIR"
else
    echo "⚠️ Папка $INSTALL_DIR уже существует."
fi

cd "$INSTALL_DIR"

# Клонирование или обновление репозитория
if [ ! -d "$INSTALL_DIR/.git" ]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  git pull
fi

echo "✅ Файлы бота скопированы!"

# Установка python-зависимостей
pip3 install --upgrade pip
pip3 install -r requirements.txt

echo "✅ Python-зависимости установлены!"

# Интерактивный ввод параметров
echo
echo "⚡️ Введите параметры для работы Telegram-бота:"
read -p "TELEGRAM_TOKEN: " TELEGRAM_TOKEN
read -p "CHAT_ID: " CHAT_ID
DEFAULT_LOG="/usr/local/x-ui/access.log"
read -p "Путь к access.log [$DEFAULT_LOG]: " ACCESS_LOG
ACCESS_LOG=${ACCESS_LOG:-$DEFAULT_LOG}
read -p "Интервал отправки сводки (например, 6h) [6h]: " SUMMARY_INTERVAL
SUMMARY_INTERVAL=${SUMMARY_INTERVAL:-6h}

# Создание .env-файла (не попадает в git)
cat <<EOF > .env
TELEGRAM_TOKEN="$TELEGRAM_TOKEN"
CHAT_ID="$CHAT_ID"
ACCESS_LOG="$ACCESS_LOG"
SUMMARY_INTERVAL="$SUMMARY_INTERVAL"
EOF

echo "✅ Конфиг сохранён ($INSTALL_DIR/.env)"

# Генерация systemd unit файла
cat <<EOF > /etc/systemd/system/xui-tg-bot.service
[Unit]
Description=XUI Telegram Bot Monitor
After=network.target

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/main.py
Restart=on-failure
User=root
EnvironmentFile=$INSTALL_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

echo "✅ systemd unit-файл создан!"

# Инициализация БД (если нужна отдельная команда — раскомментируй)
# python3 main.py --init-db

# Перезапуск и автозапуск systemd сервиса
systemctl daemon-reload
systemctl enable xui-tg-bot
systemctl restart xui-tg-bot

echo
echo "⏳ Проверка запуска Telegram-бота..."
sleep 3

# Проверка статуса
systemctl is-active --quiet xui-tg-bot && echo "✅ Бот успешно запущен и работает!" || { echo "❌ Ошибка запуска бота! Проверьте логи: journalctl -u xui-tg-bot"; exit 1; }

echo
echo "🎉 Установка завершена! Telegram-бот мониторинга 3x-ui работает."
echo "➡️ Если нужна диагностика: journalctl -u xui-tg-bot -f"
echo
