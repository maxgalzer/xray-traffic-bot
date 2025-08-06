import sqlite3
import os
import threading
import time
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# ==== Параметры ====
DB_PATH = "/opt/xray-traffic-bot/db/traffic.db"
ALERTS_PATH = "/opt/xray-traffic-bot/db/alerts.txt"
CONFIG_PATH = "/opt/xray-traffic-bot/db/config.txt"

# ==== Загрузка конфигурации ====
with open(CONFIG_PATH, "r") as f:
    TELEGRAM_TOKEN = f.readline().strip()
    CHAT_ID = f.readline().strip()
    CRON_HOURS = int(f.readline().strip())

# ==== SQLite ====
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

# ==== Алерты ====
def load_alert_domains():
    if not os.path.exists(ALERTS_PATH):
        return set()
    with open(ALERTS_PATH, "r") as f:
        return set(line.strip().lower() for line in f if line.strip())

alert_domains = load_alert_domains()
alert_enabled = True

# ==== Команды ====

def start(update: Update, context: CallbackContext):
    commands = (
        "📋 *Доступные команды:*\n\n"
        "/alertOn – включить алерты\n"
        "/alertOff – выключить алерты\n"
        "/list – список отслеживаемых доменов\n"
        "/add <домен> – добавить домен в список\n"
        "/delete <домен> – удалить домен из списка\n"
        "/find <домен> – кто и когда посещал\n"
        "/summary – прислать сводку за 6 часов\n"
        "/setcron <часы> – изменить интервал сводки\n"
    )
    update.message.reply_text(commands, parse_mode='Markdown')

def alert_on(update: Update, context: CallbackContext):
    global alert_enabled
    alert_enabled = True
    update.message.reply_text("✅ Алерты включены.")

def alert_off(update: Update, context: CallbackContext):
    global alert_enabled
    alert_enabled = False
    update.message.reply_text("✅ Алерты отключены.")

def list_domains(update: Update, context: CallbackContext):
    domains = load_alert_domains()
    if domains:
        update.message.reply_text("📌 Список доменов:\n" + "\n".join(domains))
    else:
        update.message.reply_text("📭 Список доменов пуст.")

def add_domain(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("❗ Укажите домен: /add example.com")
        return
    domain = context.args[0].strip().lower()
    with open(ALERTS_PATH, "a") as f:
        f.write(domain + "\n")
    update.message.reply_text(f"✅ Домен `{domain}` добавлен в список.", parse_mode='Markdown')

def delete_domain(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("❗ Укажите домен: /delete example.com")
        return
    domain = context.args[0].strip().lower()
    domains = load_alert_domains()
    if domain in domains:
        domains.remove(domain)
        with open(ALERTS_PATH, "w") as f:
            for d in domains:
                f.write(d + "\n")
        update.message.reply_text(f"🗑 Домен `{domain}` удалён из списка.", parse_mode='Markdown')
    else:
        update.message.reply_text(f"❌ Домен `{domain}` не найден.", parse_mode='Markdown')

def find_domain(update: Update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("❗ Укажите домен: /find example.com")
        return
    domain = context.args[0].strip().lower()
    cursor.execute("SELECT timestamp, client, inbound FROM logs WHERE domain = ? ORDER BY timestamp DESC LIMIT 10", (domain,))
    results = cursor.fetchall()
    if results:
        response = f"🔎 Последние посещения `{domain}`:\n\n"
        for ts, client, inbound in results:
            response += f"🕒 {ts}\n👤 {client}\n📥 {inbound}\n\n"
    else:
        response = f"❌ `{domain}` не найден в логах."
    update.message.reply_text(response, parse_mode='Markdown')

def summary(update: Update, context: CallbackContext):
    now = datetime.utcnow()
    since = now - timedelta(hours=6)
    cursor.execute("SELECT timestamp, client, inbound, domain FROM logs WHERE timestamp >= ?", (since.strftime("%Y-%m-%d %H:%M:%S"),))
    results = cursor.fetchall()
    if results:
        response = "📊 Последние подключения (6ч):\n\n"
        for ts, client, inbound, domain in results:
            response += f"🕒 {ts}\n👤 {client}\n📥 {inbound}\n🌐 {domain}\n\n"
    else:
        response = "Нет подключений за последние 6 часов."
    update.message.reply_text(response)

def set_cron(update: Update, context: CallbackContext):
    global CRON_HOURS
    if not context.args:
        update.message.reply_text("❗ Укажите количество часов: /setcron 3")
        return
    try:
        hours = int(context.args[0])
        if hours <= 0:
            raise ValueError
        CRON_HOURS = hours
        # Перезапись в файл
        with open(CONFIG_PATH, "w") as f:
            f.write(f"{TELEGRAM_TOKEN}\n{CHAT_ID}\n{CRON_HOURS}")
        update.message.reply_text(f"✅ Интервал крон-сводки установлен: каждые {CRON_HOURS} ч.")
    except:
        update.message.reply_text("❌ Неверный формат. Пример: /setcron 4")

# ==== Фоновая проверка алертов ====
def alert_loop():
    last_checked = datetime.utcnow() - timedelta(seconds=60)
    while True:
        time.sleep(30)
        if not alert_enabled:
            continue
        since = datetime.utcnow() - timedelta(minutes=10)
        cursor.execute("SELECT timestamp, client, inbound, domain FROM logs WHERE timestamp >= ?", (since.strftime("%Y-%m-%d %H:%M:%S"),))
        rows = cursor.fetchall()
        domains = load_alert_domains()
        for ts, client, inbound, domain in rows:
            if domain.lower() in domains:
                message = f"⚠️ Алерт!\n\n🕒 {ts}\n👤 {client}\n📥 {inbound}\n🌐 {domain}"
                context.bot.send_message(chat_id=CHAT_ID, text=message)

# ==== Запуск ====
if __name__ == '__main__':
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("alertOn", alert_on))
    dispatcher.add_handler(CommandHandler("alertOff", alert_off))
    dispatcher.add_handler(CommandHandler("list", list_domains))
    dispatcher.add_handler(CommandHandler("add", add_domain))
    dispatcher.add_handler(CommandHandler("delete", delete_domain))
    dispatcher.add_handler(CommandHandler("find", find_domain))
    dispatcher.add_handler(CommandHandler("summary", summary))
    dispatcher.add_handler(CommandHandler("setcron", set_cron))

    # Запускаем алерт-фоновый поток
    context = updater.bot
    threading.Thread(target=alert_loop, daemon=True).start()

    updater.start_polling()
    updater.idle()
