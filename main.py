import os
import sqlite3
import re
import time
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, executor
import asyncio

# --- Загрузка настроек из .env ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ACCESS_LOG = os.getenv("ACCESS_LOG", "/usr/local/x-ui/access.log")
SUMMARY_INTERVAL = os.getenv("SUMMARY_INTERVAL", "6h")
DB_PATH = os.path.join(os.path.dirname(__file__), "logs.db")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(bot)

# --- Асинхронная очередь для сообщений ---
message_queue = asyncio.Queue()

async def message_worker():
    while True:
        chat_id, msg = await message_queue.get()
        try:
            await bot.send_message(chat_id, msg, parse_mode="HTML")
        except Exception as e:
            print(f"Ошибка при отправке: {e}")

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
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('alerts_on', '1')")
    conn.commit()
    conn.close()

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

def clear_domains():
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM domains")
    conn.commit()
    conn.close()

def get_domains():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT domain FROM domains")
    domains = [row["domain"] for row in c.fetchall()]
    conn.close()
    return domains

def parse_log_line(line):
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

def tail_log():
    conn = get_db()
    c = conn.cursor()
    try:
        with open(ACCESS_LOG, "r") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(1)
                    continue
                data = parse_log_line(line)
                if not data:
                    continue
                if data["client_ip"] == "127.0.0.1":
                    continue
                c.execute('''INSERT INTO logs (log_time, client_ip, client_port, domain, protocol, inbound, client_email)
                             VALUES (?, ?, ?, ?, ?, ?, ?)''',
                          (data["log_time"], data["client_ip"], data["client_port"], data["domain"], data["protocol"], data["inbound"], data["client_email"]))
                conn.commit()
                domains = get_domains()
                alerts_on = get_setting("alerts_on", "1")
                if alerts_on == "1":
                    for dom in domains:
                        if dom and (data["domain"] == dom or data["domain"].endswith("." + dom) or dom in data["domain"]):
                            print(f"[ALERT] {data['domain']} совпал с {dom}")
                            send_alert(data)
                            break
    except Exception as e:
        print(f"Ошибка tail_log: {e}")
    finally:
        conn.close()

def send_alert(data):
    msg = (
        f"🚨 ВНИМАНИЕ: посещён отслеживаемый домен!\n"
        f"Клиент: {data['client_email']} ({data['client_ip']}:{data['client_port']})\n"
        f"Домен: {data['domain']}\n"
        f"Инбаунд: {data['inbound']}\n"
        f"Время (UTC): {convert_to_utc(data['log_time'])}"
    )
    try:
        message_queue.put_nowait((CHAT_ID, msg))
    except Exception as e:
        print(f"Ошибка при отправке алерта: {e}")

def convert_to_utc(dt_str):
    try:
        dt = datetime.strptime(dt_str.split(".")[0], "%Y/%m/%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return dt_str

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
        return 21600

def send_summary():
    conn = get_db()
    c = conn.cursor()
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
        msg = f"Сводка за последние {hours} часов\n\nНет активности пользователей."
        try:
            message_queue.put_nowait((CHAT_ID, msg))
        except Exception as e:
            print(f"Ошибка при отправке summary: {e}")
        return

    summary = {}
    for row in rows:
        key = f"{row['client_email']} ({row['inbound']})"
        if key not in summary:
            summary[key] = []
        summary[key].append(f"   {row['domain']} — {row['cnt']} раз(а)")

    msg = f"Сводка за последние {hours} часов\n\n"
    for user, doms in summary.items():
        msg += f"{user}\n"
        msg += "\n".join(doms) + "\n\n"
    try:
        message_queue.put_nowait((CHAT_ID, msg))
    except Exception as e:
        print(f"Ошибка при отправке summary: {e}")

# --- Telegram команды ---
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    print("[DEBUG] /start handler triggered")
    try:
        await message.answer(
            "👋 Бот мониторинга трафика 3x-ui.\n"
            "Доступные команды: /domains, /adddomain, /removedomain, /cleardomains, /alerts, /summary, /status"
        )
    except Exception as e:
        print(f"[ERROR] Ошибка отправки сообщения: {e}")

@dp.message_handler(commands=['domains'])
async def cmd_domains(message: types.Message):
    domains = get_domains()
    if not domains:
        await message.answer("Список отслеживаемых доменов пуст.")
    else:
        msg = "Текущие отслеживаемые домены:\n" + "\n".join([f"• {d}" for d in domains])
        await message.answer(msg)

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

@dp.message_handler(commands=['cleardomains'])
async def cmd_cleardomains(message: types.Message):
    clear_domains()
    await message.answer("Все домены удалены из отслеживания!")

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
    msg = (
        f"Статус бота:\n"
        f" • Алерты: {'ВКЛ' if alerts_on == '1' else 'ВЫКЛ'}\n"
        f" • Кол-во отслеживаемых доменов: {len(domains)}\n"
        f" • Интервал сводки: {SUMMARY_INTERVAL}\n"
    )
    await message.answer(msg)

def main():
    init_db()
    threading.Thread(target=tail_log, daemon=True).start()
    threading.Thread(target=summary_loop, daemon=True).start()
    loop = asyncio.get_event_loop()
    loop.create_task(message_worker())
    executor.start_polling(dp, skip_updates=True)

if __name__ == "__main__":
    main()
