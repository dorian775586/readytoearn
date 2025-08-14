import os
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request
from datetime import datetime

# -------------------------------
# Чтение переменных окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
admin_id_env = os.environ.get("ADMIN_ID")

try:
    ADMIN_ID = int(admin_id_env) if admin_id_env else None
except ValueError:
    print(f"Ошибка: ADMIN_ID ('{admin_id_env}') не является числом")
    ADMIN_ID = None

if not BOT_TOKEN:
    raise RuntimeError("Ошибка: BOT_TOKEN пуст или не задан!")
if not DATABASE_URL:
    raise RuntimeError("Ошибка: DATABASE_URL не задан!")

if ADMIN_ID is None:
    print("Предупреждение: ADMIN_ID не задан. Некоторые функции бота могут не работать.")
else:
    print(f"ADMIN_ID: {ADMIN_ID}")

# -------------------------------
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Инициализация базы PostgreSQL
def init_db():
    try:
        with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS tables (
                    id SERIAL PRIMARY KEY
                )
                """)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    user_name TEXT NOT NULL,
                    table_id INT NOT NULL,
                    time_slot TEXT NOT NULL,
                    booked_at TIMESTAMP NOT NULL,
                    booking_for TEXT NOT NULL,
                    phone TEXT NOT NULL
                )
                """)
                cursor.execute("""
                INSERT INTO tables (id)
                SELECT generate_series(1, 10)
                ON CONFLICT (id) DO NOTHING
                """)
            conn.commit()
        print("База данных успешно инициализирована")
    except Exception as e:
        print(f"Ошибка инициализации базы: {e}")

init_db()

# -------------------------------
# Команды бота
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "Привет! Это бот для бронирования столиков.\nИспользуй /book для брони.")

@bot.message_handler(commands=["book"])
def book(message):
    try:
        user_id = message.chat.id
        user_name = message.from_user.username or message.from_user.first_name
        table_id = 1
        time_slot = "19:00"
        booked_at = datetime.now()
        booking_for = "2 человека"
        phone = "+79990000000"

        with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO bookings (user_id, user_name, table_id, time_slot, booked_at, booking_for, phone)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (user_id, user_name, table_id, time_slot, booked_at, booking_for, phone))
            conn.commit()

        bot.send_message(user_id, f"Столик #{table_id} успешно забронирован на {time_slot}!")
        if ADMIN_ID:
            bot.send_message(ADMIN_ID, f"Новая бронь:\nПользователь: {user_name}\nСтол: {table_id}\nВремя: {time_slot}")

    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка: {e}")
        print(f"Ошибка при бронировании: {e}")

@bot.message_handler(commands=["history"])
def history(message):
    if not ADMIN_ID:
        bot.send_message(message.chat.id, "Функция истории бронирований недоступна. ADMIN_ID не задан.")
        return
    if message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, "У вас нет прав для этой команды.")
        return

    try:
        with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT booking_id, user_name, table_id, time_slot, booked_at FROM bookings ORDER BY booked_at DESC")
                rows = cursor.fetchall()

        if not rows:
            bot.send_message(ADMIN_ID, "История пуста.")
            return

        text = "История бронирований:\n\n"
        for row in rows:
            text += f"#{row['booking_id']} — {row['user_name']}, стол {row['table_id']}, время {row['time_slot']}, дата {row['booked_at']}\n"
        bot.send_message(ADMIN_ID, text)
    except Exception as e:
        bot.send_message(ADMIN_ID, f"Ошибка при получении истории: {e}")
        print(f"Ошибка истории: {e}")

# -------------------------------
# Webhook для Telegram
@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([update])
        return "OK", 200
    return "Unsupported Media Type", 415

# Главная страница для проверки
@app.route("/")
def index():
    return "Бот работает!", 200

# -------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    external_url = os.environ.get("RENDER_EXTERNAL_URL")

    if not external_url or not external_url.strip():
        raise RuntimeError("Ошибка: RENDER_EXTERNAL_URL пуст или не задан!")

    external_url = external_url.strip()
    if not external_url.startswith("https://"):
        raise RuntimeError("Ошибка: Telegram webhook требует HTTPS!")

    # Устанавливаем webhook
    bot.remove_webhook()
    webhook_url = f"{external_url}/webhook"
    try:
        bot.set_webhook(url=webhook_url)
        print(f"Webhook успешно установлен ✅ ({webhook_url})")
    except telebot.apihelper.ApiTelegramException as e:
        print("Ошибка установки webhook:", e)

    app.run(host="0.0.0.0", port=port)
