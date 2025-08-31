import os
from datetime import datetime, timedelta

import telebot
from telebot import types
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request
from flask_cors import CORS
import baza


# =========================
# ENV
# =========================
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
ADMIN_ID_ENV = (os.environ.get("ADMIN_ID") or "").strip()
WEBAPP_URL = (os.environ.get("WEBAPP_URL") or "https://gitrepo-drab.vercel.app").strip()

if not BOT_TOKEN:
    raise RuntimeError("Ошибка: BOT_TOKEN пуст или не задан!")
if not DATABASE_URL:
    raise RuntimeError("Ошибка: DATABASE_URL не задан!")

# Явноо добавим порт, если вдруг в URL его нет
if "render.com/" in DATABASE_URL and ":5432" not in DATABASE_URL:
    # перед /dbname вставим :5432, если хоста без порта
    # пример: ...render.com/whitefoxbd -> ...render.com:5432/whitefoxbd
    DATABASE_URL = DATABASE_URL.replace(".render.com/", ".render.com:5432/")

ADMIN_ID = None
if ADMIN_ID_ENV:
    try:
        ADMIN_ID = int(ADMIN_ID_ENV)
    except ValueError:
        print(f"Предупреждение: ADMIN_ID ('{ADMIN_ID_ENV}') не является числом; админ-функции отключены.")

# =========================
# BOT & APP
# =========================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)
CORS(app)

# =========================
# DB INIT
# =========================
def db_connect():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Базовые таблицы
                cur.execute("""
                CREATE TABLE IF NOT EXISTS tables (
                    id INT PRIMARY KEY
                );
                """)
                # ✅ ИСПРАВЛЕНО: используем TIMESTAMP для booking_for
                cur.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_id SERIAL PRIMARY KEY,
                    table_id INT NOT NULL,
                    time_slot TEXT NOT NULL,
                    booked_at TIMESTAMP NOT NULL,
                    booking_for TIMESTAMP,
                    phone TEXT,
                    guests INT
                );
                """)

                # На случай старой схемы — добавим недостающие поля
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS phone TEXT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS guests INT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS booking_for TIMESTAMP;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_id BIGINT;")
                cur.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS user_name TEXT;")


                # Наполним столики (если пусто)
                cur.execute("SELECT COUNT(*) AS c FROM tables;")
                c = cur.fetchone()["c"]
                if c == 0:
                    cur.execute("INSERT INTO tables (id) SELECT generate_series(1, 10);")

            conn.commit()
        print("База данных: OK")
    except Exception as e:
        print(f"Ошибка инициализации базы: {e}")


# =========================
# HELPERS (UI)
# =========================
def main_reply_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    row1 = [
        # ✅ Изменено: теперь эта кнопка открывает веб-приложение
        types.KeyboardButton("🦊 Забронировать", web_app=types.WebAppInfo(url=WEBAPP_URL)),
        types.KeyboardButton("📋 Моя бронь"),
    ]
    row2 = [types.KeyboardButton("📖 Меню")]
    kb.row(*row1)
    kb.row(*row2)
    if ADMIN_ID and user_id == ADMIN_ID:
        kb.row(types.KeyboardButton("🛠 Управление"), types.KeyboardButton("🗂 История"))
    return kb

# =========================
# COMMANDS
# =========================
@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    bot.send_photo(
        message.chat.id,
        photo="https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQbh6M8aJwxylo8aI1B-ceUHaiOyEnA425a0A&s",
        caption="<b>Рестобар «Белый Лис»</b> приветствует вас!\nТут вы можете дистанционно забронировать любой понравившийся столик!",
        reply_markup=main_reply_kb(message.from_user.id)
    )

@bot.message_handler(commands=["history"])
def cmd_history(message: types.Message):
    if not ADMIN_ID or message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, "У вас нет прав для этой команды.")
        return
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT booking_id, user_name, table_id, time_slot, booked_at, booking_for
                    FROM bookings
                    ORDER BY booked_at DESC
                    LIMIT 50;
                """)
                rows = cur.fetchall()
        if not rows:
            bot.send_message(message.chat.id, "История пуста.")
            return
        text = "<b>История бронирований (последние 50):</b>\n\n"
        for r in rows:
            # ✅ ИСПРАВЛЕНО: формат даты
            booking_date = r['booking_for'].strftime("%d.%m.%Y")
            text += f"#{r['booking_id']} — {r['user_name']}, стол {r['table_id']}, {r['time_slot']}, {booking_date}\n"
        bot.send_message(message.chat.id, text)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка истории: {e}")

# =========================
# TEXT BUTTONS
# =========================
@bot.message_handler(func=lambda m: m.text == "📋 Моя бронь")
def on_my_booking(message: types.Message):
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # ✅ ИСПРАВЛЕНО: Ищем активные брони на текущий день ИЛИ будущие
                cur.execute("""
                    SELECT booking_id, table_id, time_slot, booking_for
                    FROM bookings
                    WHERE user_id=%s AND (booking_for >= NOW() - INTERVAL '6 hours' OR DATE(booking_for) = CURRENT_DATE)
                    ORDER BY booked_at DESC
                    LIMIT 1;
                """, (message.from_user.id,))
                row = cur.fetchone()
        if not row:
            bot.send_message(message.chat.id, "У вас нет активной брони.", reply_markup=main_reply_kb(message.from_user.id))
            return
        
        # ✅ ИСПРАВЛЕНО: формат даты
        booking_date = row['booking_for'].strftime("%d.%m.%Y")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"cancel_{row['booking_id']}"))
        bot.send_message(message.chat.id, f"🔖 Ваша бронь: стол {row['table_id']} на {row['time_slot']} ({booking_date}).", reply_markup=kb)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка: {e}")

@bot.message_handler(func=lambda m: m.text == "📖 Меню")
def on_menu(message: types.Message):
    # Поставь реальные URL фото меню
    photos = [
        "https://example.com/menu1.jpg",
        "https://example.com/menu2.jpg",
    ]
    for url in photos:
        bot.send_photo(message.chat.id, photo=url)

# ✅ Изменено: теперь эта кнопка показывает список броней с кнопками отмены
@bot.message_handler(func=lambda m: m.text == "🛠 Управление")
def on_admin_panel(message: types.Message):
    if not ADMIN_ID or message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, "У вас нет прав для этой команды.")
        return
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                # Получаем все будущие бронирования
                cur.execute("""
                    SELECT booking_id, user_name, table_id, time_slot, booking_for, phone
                    FROM bookings
                    WHERE booking_for > NOW()
                    ORDER BY booking_for ASC;
                """)
                rows = cur.fetchall()
        if not rows:
            bot.send_message(message.chat.id, "Активных бронирований нет.")
            return
        
        for r in rows:
            # ✅ ИСПРАВЛЕНО: формат даты
            booking_date = r['booking_for'].strftime("%d.%m.%Y")
            text = f"🔖 Бронь #{r['booking_id']} — {r['user_name']}\n"
            text += f"  - Стол: {r['table_id']}\n"
            text += f"  - Время: {r['time_slot']} ({booking_date})\n"
            text += f"  - Телефон: {r['phone']}\n"
            
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(text="❌ Отменить", callback_data=f"admin_cancel_{r['booking_id']}"))
            bot.send_message(message.chat.id, text, reply_markup=kb)

    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка админ-панели: {e}")

@bot.message_handler(func=lambda m: m.text == "🗂 История")
def on_history_btn(message: types.Message):
    # то же, что и /history
    return cmd_history(message)

# =========================
# INLINE CALLBACKS
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("cancel_"))
def on_cancel_user(call: types.CallbackQuery):
    booking_id = int(call.data.split("_")[1])
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bookings WHERE booking_id=%s AND user_id=%s;", (booking_id, call.from_user.id))
                conn.commit()
        bot.edit_message_text("Бронь отменена.", chat_id=call.message.chat.id, message_id=call.message.id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)

# ✅ Добавлен новый обработчик для админской отмены
@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_cancel_"))
def on_cancel_admin(call: types.CallbackQuery):
    booking_id = int(call.data.split("_")[2])
    if not ADMIN_ID or call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "У вас нет прав для этого действия.", show_alert=True)
        return
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bookings WHERE booking_id=%s;", (booking_id,))
                conn.commit()
        bot.edit_message_text(f"Бронь #{booking_id} успешно отменена.", chat_id=call.message.chat.id, message_id=call.message.id)
        bot.answer_callback_query(call.id, "Бронь отменена.", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)

# =========================
# CONTACT & WEB_APP DATA
# =========================
@bot.message_handler(content_types=['web_app_data'])
def on_webapp_data(message: types.Message):
    print("ПРИШЛИ ДАННЫЕ ОТ WEBAPP:", message.web_app_data.data)
    bot.send_message(message.chat.id, "Данные получены, бронирование пока тестовое.")


# =========================
# BOOKING API (для WebApp / внешних вызовов)
# =========================
@app.route("/book", methods=["POST"])
def book_api():
    """Обработчик POST-запросов с данными из Web App"""
    try:
        # Получаем данные из запроса
        data = request.json
        # Если user_id/user_name пустые, присваиваем им значения по умолчанию
        user_id = data.get('user_id') or 0
        user_name = data.get('user_name') or 'Неизвестный'
        phone = data.get('phone')
        guests = data.get('guests')
        table_id = data.get('table')
        time_slot = data.get('time')
        date_str = data.get('date')

        # Проверяем только те поля, которые всегда должны быть
        if not all([phone, guests, table_id, time_slot, date_str]):
            return {"status": "error", "message": "Не хватает данных для бронирования"}, 400

        # Парсим дату и время для корректного формата PostgreSQL
        booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        booking_datetime = datetime.combine(booking_date, datetime.strptime(time_slot, '%H:%M').time())

        # Соединяемся с базой
        conn = psycopg2.connect(DATABASE_URL)

        # 🆕 ИСПРАВЛЕННАЯ ПРОВЕРКА НА СУЩЕСТВОВАНИЕ БРОНИ (используем booking_for)
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM bookings WHERE table_id = %s AND booking_for::date = %s AND time_slot = %s;",
                (table_id, booking_date, time_slot)
            )
            existing_booking = cursor.fetchone()
            if existing_booking:
                return {"status": "error", "message": "Этот стол уже забронирован на это время."}, 409
        
        # Записываем бронирование в базу
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bookings (user_id, user_name, phone, table_id, time_slot, guests, booked_at, booking_for)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (user_id, user_name, phone, table_id, time_slot, guests, datetime.now(), booking_datetime)
            )
            conn.commit()

        # уведомляем админа
        if ADMIN_ID:
            try:
                # ✅ ИЗМЕНЕНО: добавлена дата бронирования в уведомление
                bot.send_message(ADMIN_ID, f"Новая бронь (через API):\nПользователь: {user_name}\nСтол: {table_id}\nВремя: {time_slot}\nДата: {date_str}\nГостей: {guests}\nТелефон: {phone}")
            except Exception as e:
                print("Не удалось отправить сообщение админу:", e)

        return {"status": "ok", "message": "Бронь успешно создана"}, 200

    except Exception as e:
        print("Ошибка /book:", e)
        return {"status": "error", "message": str(e)}, 400

@app.route("/get_booked_times", methods=["GET"])
def get_booked_times():
    """Возвращает список занятых временных слотов для стола и даты"""
    try:
        table_id = request.args.get('table')
        date_str = request.args.get('date')

        if not all([table_id, date_str]):
            return {"status": "error", "message": "Не хватает данных (стол или дата)"}, 400

        # Соединяемся с базой
        conn = psycopg2.connect(DATABASE_URL)
        
        # 🆕 ИСПРАВЛЕННЫЙ ЗАПРОС (используем booking_for)
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT time_slot FROM bookings WHERE table_id = %s AND booking_for::date = %s;",
                (table_id, date_str)
            )
            booked_times = [row[0] for row in cursor.fetchall()]
        
        return {"status": "ok", "booked_times": booked_times}, 200

    except Exception as e:
        print("Ошибка /get_booked_times:", e)
        return {"status": "error", "message": str(e)}, 400

# =========================
# TELEGRAM WEBHOOK ROUTE
# =========================
@app.route("/webhook", methods=["POST"])
def webhook():
    """Обработчик обновлений, приходящих от Telegram"""
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data(as_text=True)
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    else:
        return "Invalid content type", 403


# =========================
# MAIN / WEBHOOK SETUP
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    external_url = (os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
    if not external_url:
        raise RuntimeError("Ошибка: RENDER_EXTERNAL_URL пуст или не задан!")
    if not external_url.startswith("https://"):
        raise RuntimeError("Ошибка: Telegram webhook требует HTTPS!")

    # Ставим webhook на /webhook
    try:
        bot.remove_webhook()
        webhook_url = f"{external_url}/webhook"
        ok = bot.set_webhook(url=webhook_url)
        print(f"Webhook set -> {webhook_url} ; ok={ok}")
    except telebot.apihelper.ApiTelegramException as e:
        print("Ошибка установки webhook:", e)

    app.run(host="0.0.0.0", port=port)