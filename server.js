// server.js
import express from "express";
import bodyParser from "body-parser";
import cors from "cors";
import fetch from "node-fetch";
import pkg from "pg";
const { Pool } = pkg;

const app = express();
app.use(bodyParser.json());
app.use(cors()); // Разрешаем CORS для web app

// Настройки базы PostgreSQL (Render)
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false }, // обязательно для Render
});

// Токен и чат ID Telegram бота (добавь в переменные окружения на Render)
const TELEGRAM_TOKEN = process.env.TG_TOKEN || process.env.TELEGRAM_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TG_CHAT_ID || process.env.TELEGRAM_ADMIN_CHAT_ID;

if (!TELEGRAM_TOKEN) {
  console.warn("Warning: TELEGRAM_TOKEN не задан. Отправка в Telegram отключена.");
}
if (!TELEGRAM_CHAT_ID) {
  console.warn("Warning: TELEGRAM_CHAT_ID не задан. Админ-уведомления в Telegram отключены.");
}

/**
 * Помощник: безопасно взять числовой table id
 */
function parseTableId(body) {
  const t = body.table_id ?? body.table ?? body.tableNumber ?? body.tableNumber;
  const tid = t === undefined || t === null ? null : Number(t);
  return Number.isFinite(tid) ? tid : null;
}

/**
 * Формирует ISO-строку booking_for (дата+время).
 * Если date указан — используем его; если нет — берём сегодня с time и, если прошло, добавляем 1 день.
 * date должен быть в формате YYYY-MM-DD, time в HH:MM
 */
function buildBookingFor(dateStr, timeStr) {
  if (!timeStr) return null;
  try {
    if (dateStr) {
      // Соберём ISO: "YYYY-MM-DDTHH:MM:00"
      const iso = `${dateStr}T${timeStr}:00`;
      const d = new Date(iso);
      if (!isNaN(d)) return d.toISOString();
    }
    // Нет даты — используем сегодня/завтра логику
    const now = new Date();
    const [hh, mm] = timeStr.split(":").map(Number);
    if (Number.isFinite(hh) && Number.isFinite(mm)) {
      const candidate = new Date(now);
      candidate.setHours(hh, mm, 0, 0);
      if (candidate < now) candidate.setDate(candidate.getDate() + 1);
      return candidate.toISOString();
    }
  } catch (e) {}
  return null;
}

// Маршрут для бронирования (принимает JSON от Web App)
app.options("/book", cors()); // CORS preflight
app.post("/book", async (req, res) => {
  try {
    const body = req.body || {};

    // Поддерживаем разные имена полей: table / table_id
    const table_id = parseTableId(body);
    const date = body.date ?? null;            // expected "YYYY-MM-DD" or null
    const time = body.time ?? body.time_slot ?? null; // expected "HH:MM"
    const guests = body.guests !== undefined ? Number(body.guests) : null;
    const phone = body.phone ?? null;
    const user_id = body.user_id ?? null;
    const user_name = body.user_name ?? body.userName ?? null;

    // Проверки входных данных (минимальные)
    if (!table_id || !time || !guests) {
      return res.status(400).json({ status: "error", error: "Недостаточно данных: требуется table, time и guests" });
    }
    if (Number.isNaN(guests) || guests <= 0) {
      return res.status(400).json({ status: "error", error: "Некорректное количество гостей" });
    }

    // Вычислим booking_for (timestamp) и booked_at
    const booking_for_iso = buildBookingFor(date, time); // ISO string or null
    const booked_at_iso = new Date().toISOString();

    // Сохраняем бронь в PostgreSQL
    // Обратите внимание на порядок колонок: добавил guests как отдельное поле
    const insertQuery = `
      INSERT INTO bookings (user_id, user_name, table_id, time_slot, booking_date, booked_at, booking_for, guests, phone)
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
      RETURNING booking_id;
    `;
    const values = [
      user_id || null,
      user_name || null,
      table_id,
      time,
      date || null,
      booked_at_iso,
      booking_for_iso,
      guests,
      phone || null
    ];

    const result = await pool.query(insertQuery, values);
    const bookingId = result.rows && result.rows[0] ? result.rows[0].booking_id : null;

    // Подготовим сообщение для Telegram
    const msgLines = [
      `📌 Новая бронь${bookingId ? ` #${bookingId}` : ""}:`,
      `Столик: ${table_id}`,
      `Дата: ${date || "не указана"}`,
      `Время: ${time}`,
      `Гостей: ${guests}`,
      `Телефон: ${phone || "не указан"}`,
      `Пользователь: ${user_name || "не указан"}${user_id ? ` (${user_id})` : ""}`
    ];
    const message = msgLines.join("\n");

    // Отправим уведомление админу (если указан чат)
    if (TELEGRAM_TOKEN && TELEGRAM_CHAT_ID) {
      try {
        await fetch(`https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chat_id: TELEGRAM_CHAT_ID, text: message })
        });
      } catch (tgErr) {
        console.error("Ошибка отправки в Telegram (админ):", tgErr);
      }
    }

    // По желанию — отправим подтверждение пользователю (если передали user_id и бот может ему писать)
    if (TELEGRAM_TOKEN && user_id) {
      try {
        const userMsg = `✅ Ваша бронь подтверждена: стол ${table_id}${date ? `, ${date}` : ""} в ${time}, гостей: ${guests}.`;
        await fetch(`https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chat_id: user_id, text: userMsg })
        });
      } catch (tgErr) {
        // Не критично — возможно пользователь ещё не писал боту
        console.warn("Не удалось отправить подтверждение пользователю (возможно бот не может писать):", tgErr.message || tgErr);
      }
    }

    return res.json({ status: "ok", booking_id: bookingId });
  } catch (err) {
    console.error("Ошибка /book:", err);
    return res.status(500).json({ status: "error", error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
