import os
import sqlite3
import json
from time import sleep
from datetime import datetime, timedelta
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from dotenv import load_dotenv

load_dotenv("/opt/xray-traffic-bot/.env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
LOG_PATH = os.getenv("LOG_PATH", "./access.log")
ALERTS_ENABLED = os.getenv("ALERTS_ENABLED", "true").lower() == "true"

bot = Bot(token=TELEGRAM_TOKEN)
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
dp = updater.dispatcher

conn = sqlite3.connect("/opt/xray-traffic-bot/db/traffic.db", isolation_level=None, check_same_thread=False)
cursor = conn.cursor()

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

def set_setting(key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

def get_setting(key, default=None):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default

def send_alert(domain, client, inbound):
    if get_setting("alerts_enabled", "true") == "true":
        bot.send_message(chat_id=CHAT_ID, text=f"⚠️ Клиент {client} через {inbound} посетил отслеживаемый сайт: {domain}")

def parse_line(line):
    try:
        data = json.loads(line)
        timestamp = datetime.utcnow().isoformat()
        domain = data.get("host", "")
        email = data.get("email", "unknown")
        inbound = data.get("inbound", "unknown")

        cursor.execute("INSERT INTO logs (timestamp, inbound, client, domain) VALUES (?, ?, ?, ?)",
                       (timestamp, inbound, email, domain))

        cursor.execute("SELECT domain FROM alert_domains WHERE ? LIKE '%' || domain || '%'", (domain,))
        row = cursor.fetchone()
        if row:
            send_alert(domain, email, inbound)
    except Exception:
        pass

def watch_log():
    from threading import Thread
    def run():
        with open(LOG_PATH, "r") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    sleep(1)
                    continue
                parse_line(line)
    Thread(target=run, daemon=True).start()

# Команды

def start(update: Update, context: CallbackContext):
    update.message.reply_text("""
Доступные команды:
/start - показать команды
/alertOn - включить алерты
/alertOff - отключить алерты
/list - список доменов для отслеживания
/add <домен> - добавить домен
/delete <домен> - удалить домен
/find <домен> - кто посещал
/summary - сводка за 6 часов
/setcron <cron> - изменить интервал
""")

def alert_on(update: Update, context: CallbackContext):
    set_setting("alerts_enabled", "true")
    update.message.reply_text("✅ Алерты включены")

def alert_off(update: Update, context: CallbackContext):
    set_setting("alerts_enabled", "false")
    update.message.reply_text("✅ Алерты отключены")

def list_domains(update: Update, context: CallbackContext):
    cursor.execute("SELECT domain FROM alert_domains")
    domains = cursor.fetchall()
    if domains:
        text = "📋 Список доменов:\n" + "\n".join(f"• {d[0]}" for d in domains)
    else:
        text = "Список пуст"
    update.message.reply_text(text)

def add_domain(update: Update, context: CallbackContext):
    if len(context.args) < 1:
        update.message.reply_text("⚠️ Используй: /add <домен>")
        return
    domain = context.args[0].strip()
    cursor.execute("INSERT OR IGNORE INTO alert_domains(domain) VALUES (?)", (domain,))
    update.message.reply_text(f"✅ Домен {domain} добавлен")

def delete_domain(update: Update, context: CallbackContext):
    if len(context.args) < 1:
        update.message.reply_text("⚠️ Используй: /delete <домен>")
        return
    domain = context.args[0].strip()
    cursor.execute("DELETE FROM alert_domains WHERE domain = ?", (domain,))
    update.message.reply_text(f"✅ Домен {domain} удалён")

def find_domain(update: Update, context: CallbackContext):
    if len(context.args) < 1:
        update.message.reply_text("⚠️ Используй: /find <домен>")
        return
    domain = context.args[0].strip()
    cursor.execute("SELECT timestamp, client, inbound FROM logs WHERE domain LIKE ?", (f"%{domain}%",))
    results = cursor.fetchall()
    if results:
        response = f"🔍 Посещения {domain}:\n"
        for ts, client, inbound in results:
            response += f"🕒 {ts}\n👤 {client}\n📥 {inbound}\n\n"
    else:
        response = "Ничего не найдено"
    update.message.reply_text(response)

def summary(update: Update, context: CallbackContext):
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
    update.message.reply_text(response)

def set_cron(update: Update, context: CallbackContext):
    if len(context.args) < 1:
        update.message.reply_text("⚠️ Используй: /setcron <cron выражение>")
        return
    cron = context.args[0]
    set_setting("cron", cron)
    update.message.reply_text(f"✅ Интервал обновлён: {cron}")

# Регистрация

dp.add_handler(CommandHandler("start", start))
dp.add_handler(CommandHandler("alertOn", alert_on))
dp.add_handler(CommandHandler("alertOff", alert_off))
dp.add_handler(CommandHandler("list", list_domains))
dp.add_handler(CommandHandler("add", add_domain))
dp.add_handler(CommandHandler("delete", delete_domain))
dp.add_handler(CommandHandler("find", find_domain))
dp.add_handler(CommandHandler("summary", summary))
dp.add_handler(CommandHandler("setcron", set_cron))

watch_log()
updater.start_polling()
updater.idle()
