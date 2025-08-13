import os
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request
from datetime import datetime

# Читаем токен и админа из переменных окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# -------------------------------
# Безопасное получение ADMIN_ID
admin_id_env = os.environ.get("ADMIN_ID")
try:
    ADMIN_ID = int(admin_id_env) if admin_id_env is not None else None
except ValueError:
    print(f"Ошибка: ADMIN_ID ('{admin_id_env}') не является числом")
    ADMIN_ID = None

if ADMIN_ID is None:
    print("Предупреждение: ADMIN_ID не задан. Некоторые функции бота могут не работать.")
# -------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")  # PostgreSQL URL

bot = telebot.TeleBot(BOT_TOKEN)

# Flask сервер для webhook
app = Flask(__name__)

# Инициализация базы PostgreSQL
def init_db():
    with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cursor:
            # Таблица столиков
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS tables (
                id SERIAL PRIMARY KEY
            )
            """)
            # Таблица бронирований
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
            # Заполняем 10 столиков (id от 1 до 10)
            cursor.execute("""
            INSERT INTO tables (id)
            SELECT generate_series(1, 10)
            ON CONFLICT (id) DO NOTHING
            """)
        conn.commit()

init_db()

# Команда старт
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "Привет! Это бот для бронирования столиков.\nИспользуй /book для брони.")

# Пример брони
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

# История бронирований (админ)
@bot.message_handler(commands=["history"])
def history(message):
    if not ADMIN_ID:
        bot.send_message(message.chat.id, "Функция истории бронирований недоступна. ADMIN_ID не задан.")
        return

    if message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, "У вас нет прав для этой команды.")
        return

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

# Webhook для Telegram
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("UTF-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

# Главная страница
@app.route("/")
def index():
    return "Бот работает!", 200

# Запуск
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    bot.remove_webhook()
    bot.set_webhook(url=f"https://{os.environ.get('RENDER_EXTERNAL_URL')}/{BOT_TOKEN}")
    app.run(host="0.0.0.0", port=port)
