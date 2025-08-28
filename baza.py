import os
import psycopg2

# Получаем URL базы из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")

# Подключаемся к PostgreSQL
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Создаём таблицу столиков
cursor.execute("""
CREATE TABLE IF NOT EXISTS tables (
    id SERIAL PRIMARY KEY
)
""")

# Создаём таблицу бронирований
# 🆕 ПОМЕНЯЙТЕ ЗДЕСЬ
cursor.execute("""
CREATE TABLE IF NOT EXISTS bookings (
    booking_id SERIAL PRIMARY KEY,
    table_id INTEGER NOT NULL,
    time_slot TEXT NOT NULL,
    booked_at TEXT NOT NULL,
    booking_for TEXT NOT NULL,
    phone TEXT NOT NULL
)
""")

# Заполняем 10 столиков (id от 1 до 10)
cursor.executemany("INSERT INTO tables (id) VALUES (%s) ON CONFLICT DO NOTHING", [(i,) for i in range(1, 11)])

# Сохраняем изменения и закрываем соединение
conn.commit()
conn.close()

print("База и таблицы успешно созданы и заполнены.")