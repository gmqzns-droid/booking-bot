"""Слой работы с базой данных (SQLite). v3: контакты, редактирование профиля, специальности."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = "booking.db"


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
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trainer_slot ON slots(trainer_id, slot_dt)"
        )
        # Миграции для более старых баз (например, на Railway после обновления кода)
        _ensure_column(conn, "slots", "client_username", "TEXT")


# ---------- Тренеры ----------

def register_trainer(trainer_id: int, name: str, specialty: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO trainers (id, name, specialty, created_at) "
            "VALUES (?, ?, ?, COALESCE((SELECT created_at FROM trainers WHERE id=?), ?))",
            (trainer_id, name, specialty, trainer_id, datetime.now().isoformat()),
        )


def update_trainer_profile(trainer_id: int, name: str | None = None, specialty: str | None = None):
    with get_conn() as conn:
        if name is not None:
            conn.execute("UPDATE trainers SET name=? WHERE id=?", (name, trainer_id))
        if specialty is not None:
            conn.execute("UPDATE trainers SET specialty=? WHERE id=?", (specialty, trainer_id))


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


# ---------- Слоты ----------

def add_slot(trainer_id: int, slot_dt: str) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO slots (trainer_id, slot_dt, status, created_at) VALUES (?, ?, 'free', ?)",
                (trainer_id, slot_dt, datetime.now().isoformat()),
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
            (trainer_id, datetime.now().strftime("%Y-%m-%d %H:%M"), limit_days),
        ).fetchall()
    return [r["day"] for r in rows]


def list_free_slots_for_day(trainer_id: int, day: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND status='free' "
            "AND substr(slot_dt,1,10)=? AND slot_dt >= ? ORDER BY slot_dt",
            (trainer_id, day, datetime.now().strftime("%Y-%m-%d %H:%M")),
        ).fetchall()
    return rows


def list_all_upcoming(trainer_id: int, limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND status != 'cancelled' AND slot_dt >= ? "
            "ORDER BY slot_dt LIMIT ?",
            (trainer_id, datetime.now().strftime("%Y-%m-%d %H:%M"), limit),
        ).fetchall()
    return rows


def get_slot(slot_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()


def book_slot(slot_id: int, client_id: int, client_name: str, client_username: str | None = None) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE slots SET status='booked', client_id=?, client_name=?, client_username=? "
            "WHERE id=? AND status='free'",
            (client_id, client_name, client_username, slot_id),
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
            "reminder_24h_sent=0, reminder_1h_sent=0 WHERE id=?",
            (slot_id,),
        )
        return cur.rowcount > 0


def list_client_bookings(client_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slots.*, trainers.name AS trainer_name FROM slots "
            "JOIN trainers ON trainers.id = slots.trainer_id "
            "WHERE client_id=? AND status='booked' AND slot_dt >= ? ORDER BY slot_dt",
            (client_id, datetime.now().strftime("%Y-%m-%d %H:%M")),
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
