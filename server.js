import express from "express";
import bodyParser from "body-parser";
import fetch from "node-fetch";
import pkg from "pg";
const { Pool } = pkg;

const app = express();
app.use(bodyParser.json());

// Настройки базы PostgreSQL (Render)
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false }, // обязательно для Render
});

// Токен и чат ID Telegram бота (добавь в переменные окружения на Render)
const TELEGRAM_TOKEN = process.env.TG_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TG_CHAT_ID;

// Маршрут для бронирования (принимает JSON от Web App)
app.post("/book", async (req, res) => {
  try {
    const { user_id, user_name, table, date, time, guests, phone } = req.body;

    if (!user_id || !table || !time || !guests) {
      return res.status(400).json({ status: "error", error: "Недостаточно данных для брони" });
    }

    // Сохраняем бронь в PostgreSQL
    const booked_at = new Date().toISOString();
    await pool.query(
      `INSERT INTO bookings (user_id, user_name, table_id, time_slot, booking_date, booked_at, booking_for, phone)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
      [user_id, user_name, table, time, date || null, booked_at, guests, phone || null]
    );

    // Отправляем сообщение в Telegram
    const message = `📌 Новая бронь:
Столик: ${table}
Дата: ${date || "не указана"}
Время: ${time}
На кого: ${guests}
Телефон: ${phone || "не указан"}
Пользователь: ${user_name || "не указан"} (${user_id})`;

    await fetch(`https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: TELEGRAM_CHAT_ID, text: message }),
    });

    res.json({ status: "ok", message: "Бронь успешно отправлена!" });
  } catch (err) {
    console.error(err);
    res.status(500).json({ status: "error", error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
