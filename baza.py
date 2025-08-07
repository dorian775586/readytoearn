import sqlite3

conn = sqlite3.connect("tables.db")
cursor = conn.cursor()

# Создаём таблицу столиков
cursor.execute("""
CREATE TABLE IF NOT EXISTS tables (
    id INTEGER PRIMARY KEY
)
""")

# Создаём таблицу бронирований
cursor.execute("""
CREATE TABLE IF NOT EXISTS bookings (
    booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    table_id INTEGER NOT NULL,
    time_slot TEXT NOT NULL,
    booked_at TEXT NOT NULL,
    booking_for TEXT NOT NULL,
    phone TEXT NOT NULL
)
""")

# Заполняем 10 столиков (id от 1 до 10)
cursor.executemany("INSERT OR IGNORE INTO tables (id) VALUES (?)", [(i,) for i in range(1, 11)])

conn.commit()
conn.close()

print("База и таблицы успешно созданы и заполнены.")
