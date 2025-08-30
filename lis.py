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

# Явно добавим порт, если вдруг в URL его нет
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
# HELPERS (UI)
# =========================
def main_reply_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    row1 = [
        types.KeyboardButton("🦊 Забронировать"),
        types.KeyboardButton("📋 Моя бронь"),
    ]
    row2 = [types.KeyboardButton("📖 Меню")]
    row3 = [types.KeyboardButton("🌐 Веб-интерфейс", web_app=types.WebAppInfo(url=WEBAPP_URL))]
    kb.row(*row1)
    kb.row(*row2)
    kb.row(*row3)
    if ADMIN_ID and user_id == ADMIN_ID:
        kb.row(types.KeyboardButton("🛠 Управление"), types.KeyboardButton("🗂 История"))
    return kb

def get_time_slots():
    slots = []
    start = datetime.strptime("12:00", "%H:%M")
    end = datetime.strptime("23:00", "%H:%M")
    while start <= end:
        slots.append(start.strftime("%H:%M"))
        start += timedelta(minutes=30)
    return slots

def build_tables_inline():
    markup = types.InlineKeyboardMarkup(row_width=2)
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM tables ORDER BY id;")
                rows = cur.fetchall()
        buttons = [types.InlineKeyboardButton(text=f"🪑 Стол {r['id']}", callback_data=f"book_{r['id']}") for r in rows]
        # разложим по 2 в ряд
        for i in range(0, len(buttons), 2):
            markup.row(*buttons[i:i+2])
    except Exception as e:
        print("Ошибка build_tables_inline:", e)
    return markup

def build_time_inline(table_id: int):
    markup = types.InlineKeyboardMarkup(row_width=3)
    busy = set()
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT time_slot FROM bookings
                    WHERE table_id=%s AND (booking_for IS NULL OR booking_for > NOW());
                """, (table_id,))
                rows = cur.fetchall()
                busy = {r["time_slot"] for r in rows}
    except Exception as e:
        print("Ошибка build_time_inline:", e)

    free_slots = [s for s in get_time_slots() if s not in busy]
    buttons = [types.InlineKeyboardButton(text=s, callback_data=f"time_{table_id}_{s}") for s in free_slots]
    # по 3 в ряд
    for i in range(0, len(buttons), 3):
        markup.row(*buttons[i:i+3])
    return markup

# Храним временные данные брони по юзеру (простая in-memory «FSM»)
user_flow = {}  # user_id -> {"table_id": int, "time_slot": "HH:MM", "guests": int}

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
                    SELECT booking_id, user_name, table_id, time_slot, booked_at
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
            text += f"#{r['booking_id']} — {r['user_name']}, стол {r['table_id']}, {r['time_slot']}, {r['booked_at']}\n"
        bot.send_message(message.chat.id, text)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка истории: {e}")

# =========================
# TEXT BUTTONS
# =========================
@bot.message_handler(func=lambda m: m.text == "🦊 Забронировать")
def on_book_btn(message: types.Message):
    # Проверим активную бронь
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM bookings
                    WHERE user_id=%s AND (booking_for IS NULL OR booking_for > NOW())
                    LIMIT 1;
                """, (message.from_user.id,))
                row = cur.fetchone()
        if row:
            bot.send_message(message.chat.id, "У вас уже есть активная бронь.", reply_markup=main_reply_kb(message.from_user.id))
            return
    except Exception as e:
        print("Ошибка проверки активной брони:", e)

    bot.send_message(message.chat.id, "Выберите столик:", reply_markup=build_tables_inline())

@bot.message_handler(func=lambda m: m.text == "📋 Моя бронь")
def on_my_booking(message: types.Message):
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT booking_id, table_id, time_slot
                    FROM bookings
                    WHERE user_id=%s AND (booking_for IS NULL OR booking_for::timestamp > NOW())
                    ORDER BY booked_at DESC
                    LIMIT 1;
                """, (message.from_user.id,))
                row = cur.fetchone()
        if not row:
            bot.send_message(message.chat.id, "У вас нет активной брони.", reply_markup=main_reply_kb(message.from_user.id))
            return

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(text="❌ Отменить бронь", callback_data=f"cancel_{row['booking_id']}"))
        bot.send_message(message.chat.id, f"🔖 Ваша бронь: стол {row['table_id']} на {row['time_slot']}.", reply_markup=kb)
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

@bot.message_handler(func=lambda m: m.text == "🛠 Управление")
def on_admin_panel(message: types.Message):
    if not ADMIN_ID or message.chat.id != ADMIN_ID:
        bot.send_message(message.chat.id, "У вас нет прав для этой команды.")
        return
    bot.send_message(message.chat.id, "Админ-панель: пока тут пусто 🙂", reply_markup=main_reply_kb(message.from_user.id))

@bot.message_handler(func=lambda m: m.text == "🗂 История")
def on_history_btn(message: types.Message):
    # то же, что и /history
    return cmd_history(message)

# =========================
# INLINE CALLBACKS
# =========================
@bot.callback_query_handler(func=lambda c: c.data.startswith("book_"))
def on_pick_table(call: types.CallbackQuery):
    table_id = int(call.data.split("_")[1])
    user_flow[call.from_user.id] = {"table_id": table_id}
    bot.edit_message_text(f"Стол {table_id} выбран. Выберите время:", chat_id=call.message.chat.id,
                          message_id=call.message.id, reply_markup=build_time_inline(table_id))

@bot.callback_query_handler(func=lambda c: c.data.startswith("time_"))
def on_pick_time(call: types.CallbackQuery):
    _, table_id, slot = call.data.split("_")
    u = user_flow.get(call.from_user.id, {})
    u.update({"table_id": int(table_id), "time_slot": slot})
    user_flow[call.from_user.id] = u

    # спросим гостей и номер телефона кнопкой «Отправить контакт»
    bot.edit_message_text(
        f"Вы выбрали стол {table_id} на {slot}. Сколько будет гостей?",
        chat_id=call.message.chat.id, message_id=call.message.id
    )

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(types.KeyboardButton(text="Отправить номер телефона", request_contact=True))
    bot.send_message(call.message.chat.id, "Теперь отправьте свой номер телефона:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cancel_"))
def on_cancel(call: types.CallbackQuery):
    booking_id = int(call.data.split("_")[1])
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bookings WHERE booking_id=%s;", (booking_id,))
                conn.commit()
        bot.edit_message_text("Бронь отменена.", chat_id=call.message.chat.id, message_id=call.message.id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {e}", show_alert=True)

# =========================
# CONTACT & WEB_APP DATA
# =========================
@bot.message_handler(content_types=['contact'])
def on_contact(message: types.Message):
    # ожидаем, что перед этим пользователь уже выбрал стол/время и ввёл число гостей (или пришлёт их текстом)
    # guests постараемся взять из предыдущего сообщения, если пользователь присылал число
    u = user_flow.get(message.from_user.id, {})
    if "guests" not in u:
        # попытаемся вытащить число гостей из последнего своего текста нельзя надёжно — попросим ввести явно
        bot.send_message(message.chat.id, "Укажите количество гостей (числом):")
        # пометим, что ждём гостей
        u["await_guests"] = True
        user_flow[message.from_user.id] = u
        return

    phone = message.contact.phone_number
    finalize_booking(message, phone)

@bot.message_handler(func=lambda m: True, content_types=['text'])
def on_free_text(message: types.Message):
    # перехватываем количество гостей, если мы его ждём
    u = user_flow.get(message.from_user.id, {})
    if u.get("await_guests"):
        txt = (message.text or "").strip()
        if txt.isdigit() and int(txt) > 0:
            u["guests"] = int(txt)
            u.pop("await_guests", None)
            user_flow[message.from_user.id] = u
            # теперь просим контакт
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            kb.row(types.KeyboardButton(text="Отправить номер телефона", request_contact=True))
            bot.send_message(message.chat.id, "Отлично. Теперь отправьте свой номер телефона кнопкой ниже:", reply_markup=kb)
        else:
            bot.send_message(message.chat.id, "Введите корректное число гостей (например, 2).")
        return

    # прочие тексты — игнор/возврат меню
    if message.text not in ["🦊 Забронировать", "📋 Моя бронь", "📖 Меню", "🛠 Управление", "🗂 История"]:
        bot.send_message(message.chat.id, "Выберите действие на клавиатуре ниже 👇", reply_markup=main_reply_kb(message.from_user.id))

@bot.message_handler(content_types=['web_app_data'])
def on_webapp_data(message: types.Message):
    print("ПРИШЛИ ДАННЫЕ ОТ WEBAPP:", message.web_app_data.data)
    bot.send_message(message.chat.id, "Данные получены, бронирование пока тестовое.")


def finalize_booking(message: types.Message, phone: str):
    u = user_flow.get(message.from_user.id, {})
    table_id = u.get("table_id")
    time_slot = u.get("time_slot")
    guests = u.get("guests")

    if not (table_id and time_slot and guests):
        bot.send_message(message.chat.id, "Не хватает данных для бронирования. Начните заново через «🦊 Забронировать».")
        return

    now = datetime.now()
    booking_for = now.replace(hour=int(time_slot[:2]), minute=int(time_slot[3:]), second=0, microsecond=0)
    if booking_for < now:
        booking_for += timedelta(days=1)

    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO bookings (user_id, user_name, phone, table_id, time_slot, guests, booked_at, booking_for)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """, (message.from_user.id, message.from_user.full_name, phone, table_id, time_slot, guests, datetime.now(), booking_for))
                conn.commit()
        bot.send_message(message.chat.id, f"✅ Бронь подтверждена: стол {table_id}, время {time_slot}, гостей: {guests}",
                         reply_markup=main_reply_kb(message.from_user.id))
        if ADMIN_ID:
            bot.send_message(ADMIN_ID, f"Новая бронь:\nПользователь: {message.from_user.full_name}\nСтол: {table_id}\nВремя: {time_slot}\nГостей: {guests}\nТелефон: {phone}")
        user_flow.pop(message.from_user.id, None)
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при бронировании: {e}")

# =========================
# WEBHOOK (Flask)
# =========================
@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get("content-type") != "application/json":
        return "Unsupported Media Type", 415
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

@app.route("/")
def index():
    return "Бот работает!", 200

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
        user_id = data.get('user_id') or 0         # 🆕 ИСПРАВЛЕНО
        user_name = data.get('user_name') or 'Неизвестный' # 🆕 ИСПРАВЛЕНО
        phone = data.get('phone')
        guests = data.get('guests')
        table_id = data.get('table')
        time_slot = data.get('time')
        date_str = data.get('date')

        # Проверяем только те поля, которые всегда должны быть
        if not all([phone, guests, table_id, time_slot, date_str]):
            return {"status": "error", "message": "Не хватает данных для бронирования"}, 400

        # Соединяемся с базой
        conn = psycopg2.connect(DATABASE_URL)

        # Создаем строку с описанием брони для админа
        booking_for = f"Стол {table_id} на {guests} чел. в {time_slot}"
        
        # Парсим дату и время для корректного формата PostgreSQL
        booking_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        booking_datetime = datetime.combine(booking_date, datetime.strptime(time_slot, '%H:%M').time())

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

# Добавьте этот код ниже функции book_api

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
        
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT time_slot FROM bookings WHERE table_id = %s AND booked_at::date = %s;",
                (table_id, date_str)
            )
            booked_times = [row[0] for row in cursor.fetchall()]
        
        return {"status": "ok", "booked_times": booked_times}, 200

    except Exception as e:
        print("Ошибка /get_booked_times:", e)
        return {"status": "error", "message": str(e)}, 400
        
        # уведомляем админа
        if ADMIN_ID:
            try:
                bot.send_message(ADMIN_ID, f"Новая бронь (через API):\nПользователь: {user_name}\nСтол: {table_id}\nВремя: {time_slot}\nГостей: {guests}\nТелефон: {phone}")
            except Exception as e:
                print("Не удалось отправить сообщение админу:", e)

        return {"status": "ok", "message": "Бронь успешно создана"}, 200

    except Exception as e:
        print("Ошибка /book:", e)
        return {"status": "error", "message": str(e)}, 400

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
