#!/bin/bash

set -e

GREEN='\033[1;32m'
RED='\033[1;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}🔄 Обновляем систему и устанавливаем зависимости...${NC}"
apt update -y && apt install -y python3 python3-pip curl jq sqlite3

echo -e "${YELLOW}📁 Создаём структуру проекта в /opt/xray-traffic-bot...${NC}"
mkdir -p /opt/xray-traffic-bot/{db,logs}
cd /opt/xray-traffic-bot

# 🔐 Запрос переменных
read -p "Введите TELEGRAM TOKEN: " TELEGRAM_TOKEN
read -p "Введите CHAT ID (можно узнать через @getmyid_bot): " CHAT_ID
read -p "Введите интервал для отправки сводки (формат cron, Enter для каждые 6 часов): " CRON_INPUT
CRON_EXPRESSION="${CRON_INPUT:-*/6 * * * *}"

# 💾 Сохраняем .env
cat > .env <<EOF
TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
CHAT_ID=${CHAT_ID}
ALERTS_ENABLED=true
SUMMARY_ENABLED=true
CRON_EXPRESSION="${CRON_EXPRESSION}"
LOG_PATH="/opt/xray-traffic-bot/logs/access.log"
EOF

# 📦 Python зависимости
cat > requirements.txt <<EOF
python-telegram-bot==13.15
python-dotenv
EOF

pip3 install -r requirements.txt

# 📜 Скачиваем bot.py
curl -s -o bot.py https://raw.githubusercontent.com/maxgalzer/xray-traffic-bot/main/bot.py

# 🧠 Сводка по крону
cat > summary_cron.py <<'EOF'
import os
import sqlite3
from datetime import datetime, timedelta
from telegram import Bot
from dotenv import load_dotenv

load_dotenv("/opt/xray-traffic-bot/.env")
bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))
CHAT_ID = os.getenv("CHAT_ID")

conn = sqlite3.connect("/opt/xray-traffic-bot/db/traffic.db", isolation_level=None)
cursor = conn.cursor()

now = datetime.utcnow()
since = now - timedelta(hours=6)
cursor.execute("SELECT timestamp, client, inbound, domain FROM logs WHERE timestamp >= ?", (since.isoformat(),))
results = cursor.fetchall()

if results:
    response = "📊 Последние подключения (6ч):\n\n"
    for ts, client, inbound, domain in results:
        response += f"🕒 {ts}\n👤 {client}\n📥 {inbound}\n🌐 {domain}\n\n"
else:
    response = "Нет подключений за последние 6 часов."

bot.send_message(chat_id=CHAT_ID, text=response)
EOF

chmod +x summary_cron.py

# 🗓️ Крон-задача
echo -e "${YELLOW}📅 Устанавливаем cron-задачу для сводки...${NC}"
crontab -l 2>/dev/null | grep -v 'summary_cron.py' > current_cron || true
echo "$CRON_EXPRESSION /usr/bin/python3 /opt/xray-traffic-bot/summary_cron.py" >> current_cron
crontab current_cron
rm current_cron

# 🛠️ Systemd unit
cat > /etc/systemd/system/xray-traffic-bot.service <<EOF
[Unit]
Description=XRAY Traffic Telegram Bot
After=network.target

[Service]
WorkingDirectory=/opt/xray-traffic-bot
ExecStart=/usr/bin/python3 /opt/xray-traffic-bot/bot.py
Restart=always
EnvironmentFile=/opt/xray-traffic-bot/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reexec
systemctl daemon-reload
systemctl enable xray-traffic-bot
systemctl restart xray-traffic-bot

# 🔍 ПОИСК config.json
echo -e "${YELLOW}🔎 Ищем config.json для x-ui...${NC}"
DEFAULT_CONFIG=$(find /usr/local/x-ui -name config.json 2>/dev/null | head -n1)

if [[ -z "$DEFAULT_CONFIG" ]]; then
  echo -e "${RED}❌ config.json не найден автоматически.${NC}"
  read -p "Введите путь к вашему config.json вручную: " DEFAULT_CONFIG
fi

if [[ -f "$DEFAULT_CONFIG" ]]; then
  echo -e "${GREEN}📄 Найден файл: $DEFAULT_CONFIG${NC}"
  if jq . "$DEFAULT_CONFIG" >/dev/null 2>&1; then
    echo -e "${GREEN}✅ config.json валидный JSON. Обновляем log-секцию...${NC}"
    cp "$DEFAULT_CONFIG" "$DEFAULT_CONFIG.bak.$(date +%s)"
    jq '.log = {
      access: "/opt/xray-traffic-bot/logs/access.log",
      error: "/opt/xray-traffic-bot/logs/error.log",
      loglevel: "warning"
    }' "$DEFAULT_CONFIG" > "$DEFAULT_CONFIG.tmp" && mv "$DEFAULT_CONFIG.tmp" "$DEFAULT_CONFIG"

    echo -e "${YELLOW}🔄 Перезапускаем x-ui...${NC}"
    systemctl restart x-ui || echo -e "${RED}⚠️ Не удалось перезапустить x-ui. Перезапусти вручную.${NC}"
  else
    echo -e "${RED}❌ Файл $DEFAULT_CONFIG не является валидным JSON. Лог-секция не обновлена.${NC}"
  fi
else
  echo -e "${RED}❌ Указанный путь не существует: $DEFAULT_CONFIG${NC}"
fi

# 🔎 Финальная проверка
echo -e "${YELLOW}📊 Проверка: создаём тестовую запись...${NC}"
echo '{"host":"example.com","email":"test@example.com","inbound":"test-inbound"}' >> logs/access.log

sqlite3 db/traffic.db <<EOF
CREATE TABLE IF NOT EXISTS alert_domains (id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT UNIQUE);
INSERT OR IGNORE INTO alert_domains(domain) VALUES ('example.com');
EOF

python3 summary_cron.py

sleep 3
echo -e "${GREEN}✅ Установка завершена успешно.${NC}"
echo -e "📬 Проверь Telegram: должно прийти 2 сообщения — ⚠️ алерт и 📊 сводка."
