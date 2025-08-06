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

# 🛡️ Создание таблиц (если запуск до bot.py)
cursor.execute("""
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    inbound TEXT,
    client TEXT,
    domain TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS alert_domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT UNIQUE
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

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
