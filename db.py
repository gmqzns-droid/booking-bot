"""Слой работы с базой данных (SQLite). v4: контакты, привязка клиента к тренеру, часовой пояс."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DB_PATH = "booking.db"
MSK = ZoneInfo("Europe/Moscow")


def now_msk() -> datetime:
    """Текущее время по Москве — бот считает 'сегодня/сейчас' по нему, а не по серверу."""
    return datetime.now(MSK)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn, table: str, column: str, coltype: str):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trainers (
                id INTEGER PRIMARY KEY,          -- telegram user id
                name TEXT NOT NULL,
                specialty TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trainer_id INTEGER NOT NULL,
                slot_dt TEXT NOT NULL,           -- 'YYYY-MM-DD HH:MM'
                status TEXT NOT NULL DEFAULT 'free',  -- free | booked | cancelled
                client_id INTEGER,
                client_name TEXT,
                reminder_24h_sent INTEGER NOT NULL DEFAULT 0,
                reminder_1h_sent INTEGER NOT NULL DEFAULT 0,
                no_show INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trainer_slot ON slots(trainer_id, slot_dt)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY,          -- telegram user id клиента
                trainer_id INTEGER NOT NULL,     -- "свой" тренер, к которому клиент привязан
                name TEXT,
                username TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trainer_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                price INTEGER,
                duration_min INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        # Миграции для более старых баз (например, на Railway после обновления кода)
        _ensure_column(conn, "slots", "client_username", "TEXT")
        _ensure_column(conn, "slots", "no_show", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "slots", "service_id", "INTEGER")
        _ensure_column(conn, "slots", "service_name", "TEXT")
        _ensure_column(conn, "trainers", "price", "INTEGER")
        _ensure_column(conn, "trainers", "duration_min", "INTEGER")
        _ensure_column(conn, "trainers", "category", "TEXT")
        _ensure_column(conn, "trainers", "category_key", "TEXT")


# ---------- Тренеры ----------

def register_trainer(trainer_id: int, name: str, specialty: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO trainers (id, name, specialty, created_at) "
            "VALUES (?, ?, ?, COALESCE((SELECT created_at FROM trainers WHERE id=?), ?))",
            (trainer_id, name, specialty, trainer_id, now_msk().isoformat()),
        )


def update_trainer_profile(trainer_id: int, name: str | None = None, specialty: str | None = None):
    with get_conn() as conn:
        if name is not None:
            conn.execute("UPDATE trainers SET name=? WHERE id=?", (name, trainer_id))
        if specialty is not None:
            conn.execute("UPDATE trainers SET specialty=? WHERE id=?", (specialty, trainer_id))


def set_trainer_price(trainer_id: int, price: int | None):
    """price=None — убрать цену (не показывать клиенту)."""
    with get_conn() as conn:
        conn.execute("UPDATE trainers SET price=? WHERE id=?", (price, trainer_id))


def set_trainer_duration(trainer_id: int, duration_min: int | None):
    """duration_min=None — убрать длительность (не показывать клиенту)."""
    with get_conn() as conn:
        conn.execute("UPDATE trainers SET duration_min=? WHERE id=?", (duration_min, trainer_id))


def set_trainer_category(trainer_id: int, category: str, category_key: str):
    """category — как написал сам специалист ('маникюр', 'подготовка к ЕГЭ по химии', ...),
    category_key — вычисленный бакет (fitness/beauty/tutoring/medical/other) для подбора формулировок."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE trainers SET category=?, category_key=? WHERE id=?",
            (category, category_key, trainer_id),
        )


# ---------- Услуги ----------

def add_service(trainer_id: int, name: str, price: int | None, duration_min: int | None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM services WHERE trainer_id=?",
            (trainer_id,),
        )
        pos = cur.fetchone()["pos"]
        cur = conn.execute(
            "INSERT INTO services (trainer_id, name, price, duration_min, active, position, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (trainer_id, name, price, duration_min, pos, now_msk().isoformat()),
        )
        return cur.lastrowid


def list_services(trainer_id: int, active_only: bool = True):
    with get_conn() as conn:
        q = "SELECT * FROM services WHERE trainer_id=?"
        if active_only:
            q += " AND active=1"
        q += " ORDER BY position, id"
        return conn.execute(q, (trainer_id,)).fetchall()


def get_service(service_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()


def update_service(service_id: int, name: str | None = None, price: int | None = -1, duration_min: int | None = -1):
    """price/duration_min: передавай -1, если поле не нужно менять (None — осознанно очистить)."""
    with get_conn() as conn:
        if name is not None:
            conn.execute("UPDATE services SET name=? WHERE id=?", (name, service_id))
        if price != -1:
            conn.execute("UPDATE services SET price=? WHERE id=?", (price, service_id))
        if duration_min != -1:
            conn.execute("UPDATE services SET duration_min=? WHERE id=?", (duration_min, service_id))


def delete_service(service_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE services SET active=0 WHERE id=?", (service_id,))


def count_services(trainer_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM services WHERE trainer_id=? AND active=1", (trainer_id,)
        ).fetchone()
    return row["c"] if row else 0


def get_trainer(trainer_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM trainers WHERE id=?", (trainer_id,)).fetchone()


def is_trainer(user_id: int) -> bool:
    return get_trainer(user_id) is not None


def list_trainers():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM trainers ORDER BY name").fetchall()


def list_specialties():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT specialty FROM trainers "
            "WHERE specialty IS NOT NULL AND TRIM(specialty) != '' ORDER BY specialty"
        ).fetchall()
    return [r["specialty"] for r in rows]


def list_trainers_by_specialty(specialty: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM trainers WHERE specialty=? ORDER BY name", (specialty,)
        ).fetchall()


# ---------- Привязка клиента к "своему" тренеру ----------

def link_client(client_id: int, trainer_id: int, name: str | None = None, username: str | None = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO clients (id, trainer_id, name, username, created_at) "
            "VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM clients WHERE id=?), ?))",
            (client_id, trainer_id, name, username, client_id, now_msk().isoformat()),
        )


def get_client_trainer(client_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT trainer_id FROM clients WHERE id=?", (client_id,)).fetchone()
    return row["trainer_id"] if row else None


def list_clients(trainer_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE trainer_id=? ORDER BY created_at DESC", (trainer_id,)
        ).fetchall()


def count_clients(trainer_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM clients WHERE trainer_id=?", (trainer_id,)
        ).fetchone()
    return row["c"] if row else 0


# ---------- Слоты ----------

def add_slot(trainer_id: int, slot_dt: str) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO slots (trainer_id, slot_dt, status, created_at) VALUES (?, ?, 'free', ?)",
                (trainer_id, slot_dt, now_msk().isoformat()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def list_free_days(trainer_id: int, limit_days: int = 14):
    """Дни (YYYY-MM-DD), на которые у тренера есть свободные слоты."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(slot_dt, 1, 10) AS day FROM slots "
            "WHERE trainer_id=? AND status='free' AND slot_dt >= ? ORDER BY day LIMIT ?",
            (trainer_id, now_msk().strftime("%Y-%m-%d %H:%M"), limit_days),
        ).fetchall()
    return [r["day"] for r in rows]


def list_free_slots_for_day(trainer_id: int, day: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND status='free' "
            "AND substr(slot_dt,1,10)=? AND slot_dt >= ? ORDER BY slot_dt",
            (trainer_id, day, now_msk().strftime("%Y-%m-%d %H:%M")),
        ).fetchall()
    return rows


def list_all_upcoming(trainer_id: int, limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND status != 'cancelled' AND slot_dt >= ? "
            "ORDER BY slot_dt LIMIT ?",
            (trainer_id, now_msk().strftime("%Y-%m-%d %H:%M"), limit),
        ).fetchall()
    return rows


def get_slot(slot_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()


def book_slot(
    slot_id: int,
    client_id: int,
    client_name: str,
    client_username: str | None = None,
    service_id: int | None = None,
    service_name: str | None = None,
) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE slots SET status='booked', client_id=?, client_name=?, client_username=?, "
            "service_id=?, service_name=? WHERE id=? AND status='free'",
            (client_id, client_name, client_username, service_id, service_name, slot_id),
        )
        return cur.rowcount > 0


def cancel_slot(slot_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE slots SET status='cancelled' WHERE id=?", (slot_id,))
        return cur.rowcount > 0


def free_up_slot(slot_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE slots SET status='free', client_id=NULL, client_name=NULL, client_username=NULL, "
            "service_id=NULL, service_name=NULL, reminder_24h_sent=0, reminder_1h_sent=0 WHERE id=?",
            (slot_id,),
        )
        return cur.rowcount > 0


def list_recent_past_bookings(trainer_id: int, hours: int = 48):
    """Недавно прошедшие занятые слоты (за последние `hours` часов), ещё не отмеченные неявкой —
    чтобы тренер мог отметить, что клиент не пришёл."""
    with get_conn() as conn:
        now = now_msk()
        window_start = (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        window_end = now.strftime("%Y-%m-%d %H:%M")
        rows = conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND status='booked' AND no_show=0 "
            "AND slot_dt >= ? AND slot_dt < ? ORDER BY slot_dt DESC",
            (trainer_id, window_start, window_end),
        ).fetchall()
    return rows


def mark_no_show(slot_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE slots SET no_show=1 WHERE id=? AND status='booked'", (slot_id,))
        return cur.rowcount > 0


def list_client_bookings(client_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slots.*, trainers.name AS trainer_name FROM slots "
            "JOIN trainers ON trainers.id = slots.trainer_id "
            "WHERE client_id=? AND status='booked' AND slot_dt >= ? ORDER BY slot_dt",
            (client_id, now_msk().strftime("%Y-%m-%d %H:%M")),
        ).fetchall()
    return rows


def slots_needing_reminder(field: str, window_start: str, window_end: str):
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM slots WHERE status='booked' AND {field}=0 "
            f"AND slot_dt BETWEEN ? AND ?",
            (window_start, window_end),
        ).fetchall()
    return rows


def mark_reminder_sent(slot_id: int, field: str):
    with get_conn() as conn:
        conn.execute(f"UPDATE slots SET {field}=1 WHERE id=?", (slot_id,))


def reset_all():
    """Полностью очищает всех тренеров, клиентов и записи. Необратимо — для тестирования."""
    with get_conn() as conn:
        conn.execute("DELETE FROM slots")
        conn.execute("DELETE FROM clients")
        conn.execute("DELETE FROM trainers")
