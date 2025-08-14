import os
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request
from datetime import datetime

# -------------------------------
# Чтение переменных окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Актуальная ссылка на базу PostgreSQL (Render)
DATABASE_URL = os.environ.get("DATABASE_URL") or "postgresql://whitefoxbd_user:zz8hxBjEUeLknxYVEXVh8LdgwTSK4YEh@dpg-d2ecp43ipnbc739rhgs0-a.oregon-postgres.render.com/whitefoxbd"

# Безопасное получение ADMIN_ID
admin_id_env = os.environ.get("ADMIN_ID")
try:
    ADMIN_ID = int(admin_id_env) if admin_id_env is not None else None
except ValueError:
    print(f"Ошибка: ADMIN_ID ('{admin_id_env}') не является числом")
    ADMIN_ID = None

# Проверка критичных переменных
if not BOT_TOKEN or BOT_TOKEN.strip() == "":
    raise RuntimeError("Ошибка: BOT_TOKEN пуст или не задан! Установите его в Render → Environment.")
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
        print("База данных успешно инициализирована")
    except Exception as e:
        print(f"Ошибка инициализации базы: {e}")

init_db()

# -------------------------------
# Команда /start
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(message.chat.id, "Привет! Это бот для бронирования столиков.\nИспользуй /book для брони.")

# Команда /book — пример брони
@bot.message_handler(commands=["book"])
def book(message):
    try:
        user_id = message.chat.id
        user_name = message.from_user.username or message.from_user.first_name
        table_id = 1  # Пример, можно сделать выбор
        time_slot = "19:00"  # Пример
        booked_at = datetime.now()
        booking_for = "2 человека"  # Пример
        phone = "+79990000000"  # Пример

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

# Команда /history — история бронирований (только админ)
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

# -------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    external_url = os.environ.get("RENDER_EXTERNAL_URL")

    if not external_url or external_url.strip() == "":
        raise RuntimeError("Ошибка: RENDER_EXTERNAL_URL пуст или не задан! Установите его в Render → Environment.")

    # Устанавливаем webhook
    bot.remove_webhook()
    webhook_url = f"https://{external_url}/{BOT_TOKEN}"
    success = bot.set_webhook(url=webhook_url)

    # Проверка результата
    info = bot.get_webhook_info()
    print(f"Webhook установлен? {success}")
    print(f"Текущий webhook в Telegram: {info.url}")

    app.run(host="0.0.0.0", port=port)
