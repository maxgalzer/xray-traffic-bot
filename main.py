import os
import sqlite3
import re
import time
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, executor

# --- Загрузка настроек из .env ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ACCESS_LOG = os.getenv("ACCESS_LOG", "/usr/local/x-ui/access.log")
SUMMARY_INTERVAL = os.getenv("SUMMARY_INTERVAL", "6h")
DB_PATH = os.path.join(os.path.dirname(__file__), "logs.db")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot)

# --- Работа с базой данных ---
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_time TEXT,
            client_ip TEXT,
            client_port TEXT,
            domain TEXT,
            protocol TEXT,
            inbound TEXT,
            client_email TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # alerts_on по умолчанию вкл
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('alerts_on', '1')")
    conn.commit()
    conn.close()

# --- Утилиты для работы с settings ---
def set_setting(key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else default

# --- Работа со списком доменов ---
def add_domain(domain):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT OR IGNORE INTO domains (domain) VALUES (?)", (domain.lower(),))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def remove_domain(domain):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM domains WHERE domain = ?", (domain.lower(),))
    conn.commit()
    conn.close()

def get_domains():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT domain FROM domains")
    domains = [row["domain"] for row in c.fetchall()]
    conn.close()
    return domains

# --- Парсинг access.log ---
def parse_log_line(line):
    # 2025/08/06 15:54:22.272696 from 5.167.225.135:62124 accepted tcp:speed.cloudflare.com:443 [inbound-16880 >> direct] email: 7p5uebch
    pattern = re.compile(
        r"(?P<log_time>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?).*?from (?P<client_ip>[0-9\.]+):(?P<client_port>\d+).*?accepted (?P<protocol>tcp|udp):(?P<domain>[^\s]+).*?\[(?P<inbound>[^\s\]]+)",
        re.IGNORECASE)
    email_pat = re.compile(r"email: ([a-zA-Z0-9_\-]+)")
    m = pattern.search(line)
    if m:
        log_time = m.group("log_time")
        client_ip = m.group("client_ip")
        client_port = m.group("client_port")
        protocol = m.group("protocol")
        domain = m.group("domain")
        inbound = m.group("inbound").split()[0]
        email = ""
        email_m = email_pat.search(line)
        if email_m:
            email = email_m.group(1)
        return {
            "log_time": log_time,
            "client_ip": client_ip,
            "client_port": client_port,
            "protocol": protocol,
            "domain": domain,
            "inbound": inbound,
            "client_email": email,
        }
    return None

# --- Инкрементальный парсер логов ---
def tail_log():
    conn = get_db()
    c = conn.cursor()
    # Получаем последнее время и строку
    last_id = get_setting("last_log_id", 0)
    try:
        with open(ACCESS_LOG, "r") as f:
            f.seek(0, 2)  # В конец файла
            while True:
                pos = f.tell()
                line = f.readline()
                if not line:
                    time.sleep(1)
                    continue
                data = parse_log_line(line)
                if not data:
                    continue
                # Пропускаем 127.0.0.1 и внутренние обращения
                if data["client_ip"] == "127.0.0.1":
                    continue
                # Сохраняем в БД
                c.execute('''INSERT INTO logs (log_time, client_ip, client_port, domain, protocol, inbound, client_email)
                             VALUES (?, ?, ?, ?, ?, ?, ?)''',
                          (data["log_time"], data["client_ip"], data["client_port"], data["domain"], data["protocol"], data["inbound"], data["client_email"]))
                conn.commit()
                # Алерт если домен в списке
                domains = get_domains()
                alerts_on = get_setting("alerts_on", "1")
                if alerts_on == "1":
                    for dom in domains:
                        # Точное совпадение домена или поддомен
                        if dom and (dom in data["domain"] or data["domain"] in dom):
                            send_alert(data)
                            break
    except Exception as e:
        print(f"Ошибка tail_log: {e}")
    finally:
        conn.close()

# --- Алерт в Telegram ---
def send_alert(data):
    msg = (
        f"🚨 Домен в списке!\n"
        f"Клиент: {data['client_email']} ({data['client_ip']}:{data['client_port']})\n"
        f"IP клиента: {data['client_ip']}\n"
        f"Время (UTC): {convert_to_utc(data['log_time'])}\n"
        f"Инбаунд: {data['inbound']}\n"
        f"Домен: {data['domain']}"
    )
    try:
        bot.loop.create_task(bot.send_message(CHAT_ID, msg))
    except Exception as e:
        print(f"Ошибка при отправке алерта: {e}")

# --- Вспомогательная функция: перевод времени в UTC ---
def convert_to_utc(dt_str):
    try:
        # 2025/08/06 15:54:22.272696
        dt = datetime.strptime(dt_str.split(".")[0], "%Y/%m/%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return dt_str

# --- Сводка по расписанию ---
def summary_loop():
    interval_sec = parse_interval(SUMMARY_INTERVAL)
    while True:
        send_summary()
        time.sleep(interval_sec)

def parse_interval(interval):
    if interval.endswith("h"):
        return int(interval[:-1]) * 3600
    elif interval.endswith("m"):
        return int(interval[:-1]) * 60
    elif interval.endswith("d"):
        return int(interval[:-1]) * 86400
    else:
        return 21600  # 6 часов по умолчанию

def send_summary():
    conn = get_db()
    c = conn.cursor()
    # Сводка только за последние N часов
    hours = int(float(SUMMARY_INTERVAL.strip("hm")))
    c.execute('''
        SELECT client_email, inbound, domain, COUNT(*) as cnt
        FROM logs
        WHERE log_time >= datetime('now', '-{} hours')
        GROUP BY client_email, inbound, domain
        ORDER BY client_email, inbound, cnt DESC
        LIMIT 40
    '''.format(hours))
    rows = c.fetchall()
    if not rows:
        msg = "⏱️ Сводка за последние {} часов\nНет активности.".format(hours)
        bot.loop.create_task(bot.send_message(CHAT_ID, msg))
        return

    # Формируем удобный текст
    summary = {}
    for row in rows:
        key = f"{row['client_email']} ({row['inbound']})"
        if key not in summary:
            summary[key] = []
        summary[key].append(f"- {row['domain']} ({row['cnt']} раз)")

    msg = "⏱️ Сводка за последние {} часов\n\n".format(hours)
    for user, doms in summary.items():
        msg += f"Клиент: {user}\n"
        msg += "\n".join(doms) + "\n\n"
    bot.loop.create_task(bot.send_message(CHAT_ID, msg))

# --- Telegram команды ---
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    text = (
        "Бот мониторинга трафика 3x-ui\n"
        "/domains — список доменов для алертов\n"
        "/adddomain <домен> — добавить домен\n"
        "/removedomain <домен> — удалить домен\n"
        "/alerts on|off — включить/выключить алерты\n"
        "/summary — отправить сводку\n"
        "/status — показать статус\n"
    )
    await message.answer(text)

@dp.message_handler(commands=['domains'])
async def cmd_domains(message: types.Message):
    domains = get_domains()
    if not domains:
        await message.answer("Список доменов пуст.")
    else:
        await message.answer("Текущие отслеживаемые домены:\n" + "\n".join(domains))

@dp.message_handler(commands=['adddomain'])
async def cmd_adddomain(message: types.Message):
    args = message.get_args().strip()
    if not args:
        await message.answer("Использование: /adddomain <домен>")
        return
    if add_domain(args):
        await message.answer(f"Домен {args} добавлен в отслеживаемые!")
    else:
        await message.answer("Ошибка при добавлении домена.")

@dp.message_handler(commands=['removedomain'])
async def cmd_removedomain(message: types.Message):
    args = message.get_args().strip()
    if not args:
        await message.answer("Использование: /removedomain <домен>")
        return
    remove_domain(args)
    await message.answer(f"Домен {args} удалён из отслеживаемых.")

@dp.message_handler(commands=['alerts'])
async def cmd_alerts(message: types.Message):
    args = message.get_args().strip().lower()
    if args == "on":
        set_setting("alerts_on", "1")
        await message.answer("Алерты включены.")
    elif args == "off":
        set_setting("alerts_on", "0")
        await message.answer("Алерты отключены.")
    else:
        await message.answer("Использование: /alerts on|off")

@dp.message_handler(commands=['summary'])
async def cmd_summary(message: types.Message):
    send_summary()
    await message.answer("Сводка отправлена!")

@dp.message_handler(commands=['status'])
async def cmd_status(message: types.Message):
    alerts_on = get_setting("alerts_on", "1")
    domains = get_domains()
    await message.answer(f"Статус:\nАлерты: {'ВКЛ' if alerts_on == '1' else 'ВЫКЛ'}\nОтслеживаемых доменов: {len(domains)}")

# --- Запуск потоков и бота ---
def main():
    init_db()
    threading.Thread(target=tail_log, daemon=True).start()
    threading.Thread(target=summary_loop, daemon=True).start()
    executor.start_polling(dp, skip_updates=True)

if __name__ == "__main__":
    main()
