#!/bin/bash

echo "🔧 Обновляем систему и ставим зависимости..."
apt update && apt install -y python3 python3-pip git curl

echo "📁 Создаём структуру проекта..."
mkdir -p ~/xray-traffic-bot/{bot,utils,monitor,logs}

cd ~/xray-traffic-bot || exit

echo "📦 Устанавливаем Python-библиотеки..."
cat > requirements.txt <<EOF
python-telegram-bot==20.7
EOF
pip3 install -r requirements.txt

echo "🛠️ Создаём config.json..."
cat > config.json <<EOF
{
  "telegram_token": "",
  "chat_id": "",
  "alert_domains": [],
  "alerts_enabled": true,
  "summary_enabled": true,
  "cron_interval_hours": 6
}
EOF

echo "✅ Готово. Введите токен и chat_id в config.json перед запуском."
