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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL,   -- = trainers.id владельца
                name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_id INTEGER NOT NULL,   -- = trainers.id владельца сети
                name TEXT NOT NULL,
                address TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS client_links (
                client_id INTEGER NOT NULL,
                trainer_id INTEGER NOT NULL,
                name TEXT,
                username TEXT,
                custom_name TEXT,
                blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_opened_at TEXT,
                PRIMARY KEY (client_id, trainer_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS waitlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trainer_id INTEGER NOT NULL,
                staff_id INTEGER NOT NULL,
                staff_name TEXT,
                client_id INTEGER NOT NULL,
                client_name TEXT,
                client_username TEXT,
                service_id INTEGER,
                service_name TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_waitlist_staff_client "
            "ON waitlist(trainer_id, staff_id, client_id)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_id INTEGER NOT NULL UNIQUE,
                trainer_id INTEGER NOT NULL,
                staff_id INTEGER,
                staff_name TEXT,
                client_id INTEGER NOT NULL,
                client_name TEXT,
                rating INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _ensure_column(conn, "slots", "review_requested", "INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trainer_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                discount_type TEXT NOT NULL,   -- percent | fixed | free
                discount_value INTEGER,        -- % (1-100) или рубли; NULL для free
                max_uses INTEGER,              -- NULL = без ограничения
                used_count INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_trainer_code ON promo_codes(trainer_id, code)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                promo_id INTEGER NOT NULL,
                client_id INTEGER NOT NULL,
                slot_id INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_redeem_client "
            "ON promo_redemptions(promo_id, client_id)"
        )
        _ensure_column(conn, "slots", "promo_code", "TEXT")
        _ensure_column(conn, "slots", "discount_label", "TEXT")
        # Миграции для более старых баз (например, на Railway после обновления кода)
        _ensure_column(conn, "slots", "client_username", "TEXT")
        _ensure_column(conn, "slots", "no_show", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "slots", "service_id", "INTEGER")
        _ensure_column(conn, "slots", "service_name", "TEXT")
        _ensure_column(conn, "slots", "staff_id", "INTEGER")
        _ensure_column(conn, "slots", "staff_name", "TEXT")
        _ensure_column(conn, "clients", "blocked", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "trainers", "cancel_min_hours", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "trainers", "price", "INTEGER")
        _ensure_column(conn, "trainers", "duration_min", "INTEGER")
        _ensure_column(conn, "trainers", "category", "TEXT")
        _ensure_column(conn, "trainers", "category_key", "TEXT")
        _ensure_column(conn, "trainers", "is_business", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "trainers", "address", "TEXT")
        _ensure_column(conn, "clients", "custom_name", "TEXT")
        _ensure_column(conn, "slots", "client_custom_name", "TEXT")
        _ensure_column(conn, "staff", "branch_id", "INTEGER")

        # Миграция: раньше clients.id был первичным ключом (один клиент — только ОДИН
        # специалист одновременно, вторая привязка тихо затирала первую). Переносим
        # накопленные строки в client_links (составной ключ client_id+trainer_id), где
        # клиент может быть привязан сразу к нескольким специалистам. Разовая операция —
        # выполняется только если client_links ещё пустая, а старые данные есть.
        already_migrated = conn.execute("SELECT 1 FROM client_links LIMIT 1").fetchone()
        old_clients_exist = conn.execute("SELECT 1 FROM clients LIMIT 1").fetchone()
        if not already_migrated and old_clients_exist:
            conn.execute(
                "INSERT OR IGNORE INTO client_links "
                "(client_id, trainer_id, name, username, custom_name, blocked, created_at, last_opened_at) "
                "SELECT id, trainer_id, name, username, custom_name, blocked, created_at, created_at FROM clients"
            )

        # У слотов, заведённых до появления сотрудников, уникальный индекс был на
        # (trainer_id, slot_dt) — теперь то же самое время может быть свободно у РАЗНЫХ
        # сотрудников одного бизнеса, поэтому индекс должен учитывать staff_id.
        conn.execute("DROP INDEX IF EXISTS idx_trainer_slot")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trainer_staff_slot "
            "ON slots(trainer_id, staff_id, slot_dt)"
        )


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


def set_trainer_cancel_min_hours(trainer_id: int, hours: int):
    with get_conn() as conn:
        conn.execute("UPDATE trainers SET cancel_min_hours=? WHERE id=?", (max(0, hours), trainer_id))


def set_trainer_is_business(trainer_id: int, is_business: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE trainers SET is_business=? WHERE id=?", (1 if is_business else 0, trainer_id)
        )


# ---------- Филиалы (для бизнес-аккаунтов с сетью — необязательная надстройка;
# бизнес без единого добавленного филиала работает как раньше, одной локацией) ----------

def add_branch(business_id: int, name: str, address: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM branches WHERE business_id=?",
            (business_id,),
        )
        pos = cur.fetchone()["pos"]
        cur = conn.execute(
            "INSERT INTO branches (business_id, name, address, active, position, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (business_id, name, address, pos, now_msk().isoformat()),
        )
        return cur.lastrowid


def list_branches(business_id: int, active_only: bool = True):
    with get_conn() as conn:
        q = "SELECT * FROM branches WHERE business_id=?"
        if active_only:
            q += " AND active=1"
        q += " ORDER BY position, id"
        return conn.execute(q, (business_id,)).fetchall()


def get_branch(branch_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()


def update_branch(branch_id: int, name: str | None = None, address: str | None = None):
    with get_conn() as conn:
        if name is not None:
            conn.execute("UPDATE branches SET name=? WHERE id=?", (name, branch_id))
        if address is not None:
            conn.execute("UPDATE branches SET address=? WHERE id=?", (address, branch_id))


def delete_branch(branch_id: int):
    """Отключает филиал; закреплённых за ним сотрудников открепляет (branch_id=NULL),
    а не удаляет — они остаются в общем списке без привязки к филиалу."""
    with get_conn() as conn:
        conn.execute("UPDATE staff SET branch_id=NULL WHERE branch_id=?", (branch_id,))
        conn.execute("UPDATE branches SET active=0 WHERE id=?", (branch_id,))


def count_branches(business_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM branches WHERE business_id=? AND active=1", (business_id,)
        ).fetchone()
    return row["c"] if row else 0


# ---------- Сотрудники (актуально для бизнес-аккаунтов; у соло-специалиста
# всегда ровно одна запись здесь, заведённая автоматически при регистрации) ----------

_UNSET = object()
UNSET = _UNSET  # публичный алиас — вызывающий код (webapp.py) использует его как "не менять"


def add_staff(business_id: int, name: str, branch_id: int | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM staff WHERE business_id=?",
            (business_id,),
        )
        pos = cur.fetchone()["pos"]
        cur = conn.execute(
            "INSERT INTO staff (business_id, name, branch_id, active, position, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (business_id, name, branch_id, pos, now_msk().isoformat()),
        )
        return cur.lastrowid


def list_staff(business_id: int, active_only: bool = True, branch_id=_UNSET):
    with get_conn() as conn:
        q = "SELECT * FROM staff WHERE business_id=?"
        params = [business_id]
        if active_only:
            q += " AND active=1"
        if branch_id is not _UNSET:
            q += " AND branch_id IS ?"
            params.append(branch_id)
        q += " ORDER BY position, id"
        return conn.execute(q, params).fetchall()


def get_staff(staff_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()


def update_staff(staff_id: int, name: str | None = None, branch_id=_UNSET):
    with get_conn() as conn:
        if name is not None:
            conn.execute("UPDATE staff SET name=? WHERE id=?", (name, staff_id))
        if branch_id is not _UNSET:
            conn.execute("UPDATE staff SET branch_id=? WHERE id=?", (branch_id, staff_id))


def delete_staff(staff_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE staff SET active=0 WHERE id=?", (staff_id,))


def count_staff(business_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM staff WHERE business_id=? AND active=1", (business_id,)
        ).fetchone()
    return row["c"] if row else 0


# ---------- Лист ожидания ----------
# Клиент встаёт в очередь на конкретного сотрудника, если у него нет свободного
# времени; при освобождении слота (отмена клиентом или новый слот от специалиста)
# всем ожидающим шлётся уведомление, а лист для этого сотрудника очищается —
# кто ещё хочет ждать, встанет заново по следующему разу.

def join_waitlist(
    trainer_id: int, staff_id: int, staff_name: str,
    client_id: int, client_name: str, client_username: str | None,
    service_id: int | None = None, service_name: str | None = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO waitlist (trainer_id, staff_id, staff_name, client_id, client_name, "
            "client_username, service_id, service_name, created_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(trainer_id, staff_id, client_id) DO UPDATE SET "
            "service_id=excluded.service_id, service_name=excluded.service_name, "
            "created_at=excluded.created_at",
            (trainer_id, staff_id, staff_name, client_id, client_name, client_username,
             service_id, service_name, now_msk().isoformat()),
        )
        return cur.lastrowid


def leave_waitlist(trainer_id: int, staff_id: int, client_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM waitlist WHERE trainer_id=? AND staff_id=? AND client_id=?",
            (trainer_id, staff_id, client_id),
        )
        return cur.rowcount > 0


def get_waitlist_entry(trainer_id: int, staff_id: int, client_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM waitlist WHERE trainer_id=? AND staff_id=? AND client_id=?",
            (trainer_id, staff_id, client_id),
        ).fetchone()


def pop_waitlist_for_staff(trainer_id: int, staff_id: int):
    """Возвращает всех, кто ждёт свободного времени у этого сотрудника, и сразу убирает
    их из листа — уведомление шлётся один раз на освобождение, а не при каждой мелочи."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM waitlist WHERE trainer_id=? AND staff_id=? ORDER BY created_at",
            (trainer_id, staff_id),
        ).fetchall()
        conn.execute(
            "DELETE FROM waitlist WHERE trainer_id=? AND staff_id=?", (trainer_id, staff_id)
        )
    return rows


# ---------- Отзывы после визита ----------

def slots_needing_review(hours_after: int = 2, max_age_hours: int = 26):
    """Прошедшие визиты (booked, не неявка), которым пора спросить оценку: время сеанса
    было хотя бы `hours_after` часов назад, но не больше `max_age_hours` (чтобы не заваливать
    клиента древними просьбами после долгого простоя/редеплоя)."""
    with get_conn() as conn:
        now = now_msk()
        cutoff = (now - timedelta(hours=hours_after)).strftime("%Y-%m-%d %H:%M")
        floor = (now - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M")
        rows = conn.execute(
            "SELECT * FROM slots WHERE status='booked' AND no_show=0 AND review_requested=0 "
            "AND slot_dt <= ? AND slot_dt > ?",
            (cutoff, floor),
        ).fetchall()
    return rows


def mark_review_requested(slot_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE slots SET review_requested=1 WHERE id=?", (slot_id,))


def add_review(
    slot_id: int, trainer_id: int, staff_id: int | None, staff_name: str | None,
    client_id: int, client_name: str, rating: int,
) -> int | None:
    """Возвращает id созданного отзыва, либо None, если отзыв на этот слот уже есть."""
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO reviews (slot_id, trainer_id, staff_id, staff_name, client_id, "
                "client_name, rating, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (slot_id, trainer_id, staff_id, staff_name, client_id, client_name, rating,
                 now_msk().isoformat()),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def set_review_comment(review_id: int, comment: str):
    with get_conn() as conn:
        conn.execute("UPDATE reviews SET comment=? WHERE id=?", (comment, review_id))


def get_review(review_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()


def trainer_rating_summary(trainer_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c, AVG(rating) AS avg FROM reviews WHERE trainer_id=?",
            (trainer_id,),
        ).fetchone()
    return {"count": row["c"] or 0, "avg": round(row["avg"], 1) if row["avg"] else None}


def list_reviews_for_trainer(trainer_id: int, limit: int = 30):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reviews WHERE trainer_id=? ORDER BY created_at DESC LIMIT ?",
            (trainer_id, limit),
        ).fetchall()
    return rows


# ---------- Промокоды ----------
# Бот не проводит платежи — специалист сам решает, как применить скидку на месте.
# Промокод здесь про проверку и учёт использования, а не про расчёт денег.

def add_promo(
    trainer_id: int, code: str, discount_type: str,
    discount_value: int | None, max_uses: int | None,
) -> int | None:
    """Возвращает id, либо None если у этого специалиста уже есть код с таким названием."""
    code = (code or "").strip().upper()[:20]
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO promo_codes (trainer_id, code, discount_type, discount_value, "
                "max_uses, used_count, active, created_at) VALUES (?,?,?,?,?,0,1,?)",
                (trainer_id, code, discount_type, discount_value, max_uses, now_msk().isoformat()),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def list_promos(trainer_id: int, active_only: bool = False):
    with get_conn() as conn:
        q = "SELECT * FROM promo_codes WHERE trainer_id=?"
        if active_only:
            q += " AND active=1"
        q += " ORDER BY id DESC"
        return conn.execute(q, (trainer_id,)).fetchall()


def get_promo(promo_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM promo_codes WHERE id=?", (promo_id,)).fetchone()


def get_active_promo_by_code(trainer_id: int, code: str):
    code = (code or "").strip().upper()
    if not code:
        return None
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM promo_codes WHERE trainer_id=? AND code=? AND active=1",
            (trainer_id, code),
        ).fetchone()


def delete_promo(promo_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE promo_codes SET active=0 WHERE id=?", (promo_id,))


def has_client_used_promo(promo_id: int, client_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM promo_redemptions WHERE promo_id=? AND client_id=?",
            (promo_id, client_id),
        ).fetchone()
    return row is not None


def set_slot_promo(slot_id: int, promo_code: str, discount_label: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE slots SET promo_code=?, discount_label=? WHERE id=?",
            (promo_code, discount_label, slot_id),
        )


def redeem_promo(promo_id: int, client_id: int, slot_id: int) -> bool:
    """False, если этот клиент уже использовал этот код раньше (гонка/повторный сабмит)."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO promo_redemptions (promo_id, client_id, slot_id, created_at) "
                "VALUES (?,?,?,?)",
                (promo_id, client_id, slot_id, now_msk().isoformat()),
            )
            conn.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE id=?", (promo_id,))
            return True
        except sqlite3.IntegrityError:
            return False


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


# ---------- Привязка клиента к специалисту(-ам) ----------
# Один и тот же человек в Telegram может быть клиентом сразу нескольких специалистов,
# использующих сервис, — client_links хранит эту связь как (client_id, trainer_id),
# а не как одну запись на клиента, поэтому открытие ссылки одного мастера не отвязывает
# от другого. last_opened_at отмечает, какую из связей клиент открывал последней —
# по нему определяется, чей кабинет показать, когда клиент открывает приложение
# не по конкретной ссылке (например, с постоянной кнопки).

def link_client(client_id: int, trainer_id: int, name: str | None = None, username: str | None = None):
    now = now_msk().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO client_links (client_id, trainer_id, name, username, created_at, last_opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(client_id, trainer_id) DO UPDATE SET "
            "name=excluded.name, username=excluded.username, last_opened_at=excluded.last_opened_at",
            (client_id, trainer_id, name, username, now, now),
        )


def touch_client_link(client_id: int, trainer_id: int):
    """Отмечает связь как последнюю открытую — используется, когда клиент явно выбирает,
    к какому из своих специалистов зайти (переключатель в приложении)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE client_links SET last_opened_at=? WHERE client_id=? AND trainer_id=?",
            (now_msk().isoformat(), client_id, trainer_id),
        )


def get_client_trainer(client_id: int):
    """Тренер последней открытой связи клиента (для обратной совместимости с местами,
    которым важен только 'текущий' бизнес). Для полного списка — list_client_links."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT trainer_id FROM client_links WHERE client_id=? "
            "ORDER BY last_opened_at DESC, created_at DESC LIMIT 1",
            (client_id,),
        ).fetchone()
    return row["trainer_id"] if row else None


def list_client_links(client_id: int):
    """Все специалисты, к которым привязан этот клиент, самый недавно открытый — первым."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT cl.*, trainers.name AS trainer_name FROM client_links cl "
            "JOIN trainers ON trainers.id = cl.trainer_id "
            "WHERE cl.client_id=? ORDER BY cl.last_opened_at DESC, cl.created_at DESC",
            (client_id,),
        ).fetchall()


def list_clients(trainer_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT client_id AS id, trainer_id, name, username, custom_name, blocked, created_at "
            "FROM client_links WHERE trainer_id=? ORDER BY created_at DESC", (trainer_id,)
        ).fetchall()


def next_bookings_by_client(trainer_id: int) -> dict:
    """Для каждого клиента тренера — его ближайшая предстоящая запись (booked, slot_dt >= сейчас).
    Возвращает {client_id: slot_row}."""
    with get_conn() as conn:
        now = now_msk().strftime("%Y-%m-%d %H:%M")
        rows = conn.execute(
            """
            SELECT s.* FROM slots s
            INNER JOIN (
                SELECT client_id, MIN(slot_dt) AS min_dt FROM slots
                WHERE trainer_id=? AND status='booked' AND slot_dt >= ? AND client_id IS NOT NULL
                GROUP BY client_id
            ) nxt ON s.client_id = nxt.client_id AND s.slot_dt = nxt.min_dt
            WHERE s.trainer_id=? AND s.status='booked'
            """,
            (trainer_id, now, trainer_id),
        ).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["client_id"], row)
    return result


def set_client_blocked(trainer_id: int, client_id: int, blocked: bool) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE client_links SET blocked=? WHERE client_id=? AND trainer_id=?",
            (1 if blocked else 0, client_id, trainer_id),
        )
        return cur.rowcount > 0


def is_client_blocked(trainer_id: int, client_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT blocked FROM client_links WHERE client_id=? AND trainer_id=?", (client_id, trainer_id)
        ).fetchone()
    return bool(row["blocked"]) if row else False


def count_clients(trainer_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM client_links WHERE trainer_id=?", (trainer_id,)
        ).fetchone()
    return row["c"] if row else 0


# ---------- Слоты ----------

def add_slot(trainer_id: int, staff_id: int, staff_name: str, slot_dt: str) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO slots (trainer_id, staff_id, staff_name, slot_dt, status, created_at) "
                "VALUES (?, ?, ?, ?, 'free', ?)",
                (trainer_id, staff_id, staff_name, slot_dt, now_msk().isoformat()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def list_free_days(trainer_id: int, staff_id: int, limit_days: int = 14):
    """Дни (YYYY-MM-DD), на которые у конкретного сотрудника есть свободные слоты."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(slot_dt, 1, 10) AS day FROM slots "
            "WHERE trainer_id=? AND staff_id=? AND status='free' AND slot_dt >= ? "
            "ORDER BY day LIMIT ?",
            (trainer_id, staff_id, now_msk().strftime("%Y-%m-%d %H:%M"), limit_days),
        ).fetchall()
    return [r["day"] for r in rows]


def list_free_slots_for_day(trainer_id: int, staff_id: int, day: str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND staff_id=? AND status='free' "
            "AND substr(slot_dt,1,10)=? AND slot_dt >= ? ORDER BY slot_dt",
            (trainer_id, staff_id, day, now_msk().strftime("%Y-%m-%d %H:%M")),
        ).fetchall()
    return rows


def trainer_stats(trainer_id: int, staff_id: int | None, days: int) -> dict:
    """Статистика мастера за последние `days` дней: визиты, выручка (по текущей цене услуги
    на момент запроса — оплату бот не проводит, это ориентир), неявки. staff_id=None — по
    всем сотрудникам сразу (агрегат по бизнесу)."""
    with get_conn() as conn:
        since = (now_msk() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
        until = now_msk().strftime("%Y-%m-%d %H:%M")
        params: list = [trainer_id, since, until]
        staff_clause = ""
        if staff_id:
            staff_clause = "AND slots.staff_id=?"
            params.append(staff_id)
        rows = conn.execute(
            f"SELECT slots.no_show AS no_show, services.price AS price FROM slots "
            f"LEFT JOIN services ON services.id = slots.service_id "
            f"WHERE slots.trainer_id=? AND slots.status='booked' "
            f"AND slots.slot_dt >= ? AND slots.slot_dt < ? {staff_clause}",
            params,
        ).fetchall()
    visits = sum(1 for r in rows if not r["no_show"])
    no_shows = sum(1 for r in rows if r["no_show"])
    revenue = sum((r["price"] or 0) for r in rows if not r["no_show"])
    total = visits + no_shows
    return {
        "visits": visits,
        "no_shows": no_shows,
        "no_show_rate": round(100 * no_shows / total) if total else 0,
        "revenue": revenue,
        "avg_check": round(revenue / visits) if visits else 0,
    }


def list_slots_in_range(trainer_id: int, staff_id: int, start_date: str, end_date: str):
    """Все ещё активные (free+booked) слоты сотрудника в диапазоне дат [start_date, end_date]
    включительно — используется для массового закрытия периода (отпуск/выходной)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND staff_id=? AND status IN ('free','booked') "
            "AND substr(slot_dt,1,10) BETWEEN ? AND ? ORDER BY slot_dt",
            (trainer_id, staff_id, start_date, end_date),
        ).fetchall()
    return rows


def list_all_upcoming(trainer_id: int, staff_id: int, limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND staff_id=? AND status != 'cancelled' "
            "AND slot_dt >= ? ORDER BY slot_dt LIMIT ?",
            (trainer_id, staff_id, now_msk().strftime("%Y-%m-%d %H:%M"), limit),
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
    client_custom_name: str | None = None,
) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE slots SET status='booked', client_id=?, client_name=?, client_username=?, "
            "service_id=?, service_name=?, client_custom_name=? WHERE id=? AND status='free'",
            (client_id, client_name, client_username, service_id, service_name, client_custom_name, slot_id),
        )
        return cur.rowcount > 0


def set_client_custom_name(client_id: int, trainer_id: int, custom_name: str | None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE client_links SET custom_name=? WHERE client_id=? AND trainer_id=?",
            (custom_name, client_id, trainer_id),
        )


def get_client_custom_name(client_id: int, trainer_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT custom_name FROM client_links WHERE client_id=? AND trainer_id=?",
            (client_id, trainer_id),
        ).fetchone()
    return row["custom_name"] if row else None


def set_trainer_address(trainer_id: int, address: str | None):
    with get_conn() as conn:
        conn.execute("UPDATE trainers SET address=? WHERE id=?", (address, trainer_id))


def find_overlapping_booked_slot(
    trainer_id: int, staff_id: int, slot_dt: str, duration_min: int | None, exclude_slot_id: int | None = None
):
    """Ищет уже забронированный слот того же сотрудника, чьё время пересекается с окном
    [slot_dt, slot_dt+duration_min). Слоты расставляет мастер вручную как отдельные точки
    времени, а не сеткой по длительности услуги — поэтому если услуга длинная (например 60 мин),
    а слоты стоят через 30, эта проверка не даёт забронировать два визита, которые физически
    наложатся друг на друга. Услуга без указанной длительности не блокирует соседей (как раньше)."""
    if not duration_min:
        return None
    start = datetime.strptime(slot_dt, "%Y-%m-%d %H:%M")
    end = start + timedelta(minutes=duration_min)
    window_start = (start - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")
    window_end = (end + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slots.*, services.duration_min AS svc_duration FROM slots "
            "LEFT JOIN services ON services.id = slots.service_id "
            "WHERE slots.trainer_id=? AND slots.staff_id=? AND slots.status='booked' "
            "AND slots.slot_dt >= ? AND slots.slot_dt <= ?",
            (trainer_id, staff_id, window_start, window_end),
        ).fetchall()
    for r in rows:
        if exclude_slot_id and r["id"] == exclude_slot_id:
            continue
        other_start = datetime.strptime(r["slot_dt"], "%Y-%m-%d %H:%M")
        other_duration = r["svc_duration"] or 0
        other_end = other_start + timedelta(minutes=other_duration) if other_duration else other_start + timedelta(minutes=1)
        if start < other_end and other_start < end:
            return r
    return None


def get_slot_by_dt(trainer_id: int, staff_id: int, slot_dt: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND staff_id=? AND slot_dt=?",
            (trainer_id, staff_id, slot_dt),
        ).fetchone()


def reschedule_slot(old_slot_id: int, new_slot_id: int) -> bool:
    """Переносит существующую бронь на другой слот того же мастера/сотрудника: копирует
    клиента, услугу и промокод на новый (свободный) слот и освобождает старый. Атомарно —
    обе строки обновляются в одной транзакции, чтобы никогда не потерять бронь между шагами."""
    with get_conn() as conn:
        old = conn.execute("SELECT * FROM slots WHERE id=?", (old_slot_id,)).fetchone()
        new = conn.execute("SELECT * FROM slots WHERE id=?", (new_slot_id,)).fetchone()
        if not old or not new:
            return False
        if old["status"] != "booked" or new["status"] != "free":
            return False
        if old["trainer_id"] != new["trainer_id"] or old["staff_id"] != new["staff_id"]:
            return False
        cur = conn.execute(
            "UPDATE slots SET status='booked', client_id=?, client_name=?, client_username=?, "
            "service_id=?, service_name=?, promo_code=?, discount_label=?, client_custom_name=?, "
            "reminder_24h_sent=0, reminder_1h_sent=0, no_show=0 WHERE id=? AND status='free'",
            (
                old["client_id"], old["client_name"], old["client_username"],
                old["service_id"], old["service_name"], old["promo_code"], old["discount_label"],
                old["client_custom_name"] if "client_custom_name" in old.keys() else None,
                new_slot_id,
            ),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "UPDATE slots SET status='free', client_id=NULL, client_name=NULL, client_username=NULL, "
            "service_id=NULL, service_name=NULL, promo_code=NULL, discount_label=NULL, client_custom_name=NULL, "
            "reminder_24h_sent=0, reminder_1h_sent=0, no_show=0, review_requested=0 WHERE id=?",
            (old_slot_id,),
        )
        return True


def cancel_slot(slot_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE slots SET status='cancelled' WHERE id=?", (slot_id,))
        return cur.rowcount > 0


def free_up_slot(slot_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE slots SET status='free', client_id=NULL, client_name=NULL, client_username=NULL, "
            "service_id=NULL, service_name=NULL, client_custom_name=NULL, "
            "reminder_24h_sent=0, reminder_1h_sent=0 WHERE id=?",
            (slot_id,),
        )
        return cur.rowcount > 0


def list_recent_past_bookings(trainer_id: int, staff_id: int, hours: int = 48):
    """Недавно прошедшие занятые слоты конкретного сотрудника (за последние `hours` часов),
    ещё не отмеченные неявкой — чтобы можно было отметить, что клиент не пришёл."""
    with get_conn() as conn:
        now = now_msk()
        window_start = (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        window_end = now.strftime("%Y-%m-%d %H:%M")
        rows = conn.execute(
            "SELECT * FROM slots WHERE trainer_id=? AND staff_id=? AND status='booked' AND no_show=0 "
            "AND slot_dt >= ? AND slot_dt < ? ORDER BY slot_dt DESC",
            (trainer_id, staff_id, window_start, window_end),
        ).fetchall()
    return rows


def mark_no_show(slot_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE slots SET no_show=1 WHERE id=? AND status='booked'", (slot_id,))
        return cur.rowcount > 0


def list_client_bookings(client_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slots.*, trainers.name AS trainer_name, "
            "COALESCE(branches.address, trainers.address) AS trainer_address, "
            "branches.name AS branch_name "
            "FROM slots "
            "JOIN trainers ON trainers.id = slots.trainer_id "
            "LEFT JOIN staff ON staff.id = slots.staff_id "
            "LEFT JOIN branches ON branches.id = staff.branch_id "
            "WHERE client_id=? AND status='booked' AND slot_dt >= ? ORDER BY slot_dt",
            (client_id, now_msk().strftime("%Y-%m-%d %H:%M")),
        ).fetchall()
    return rows


def list_client_past_bookings(client_id: int, limit: int = 10):
    """История прошедших визитов клиента (в т.ч. отменённых до начала) — используется для
    повтора записи в один тап. Берём и booked (уже прошедшие), и cancelled — но не 'free'
    (это была бы чужая история, а не клиента)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slots.*, trainers.name AS trainer_name, "
            "COALESCE(branches.address, trainers.address) AS trainer_address, "
            "branches.name AS branch_name "
            "FROM slots "
            "JOIN trainers ON trainers.id = slots.trainer_id "
            "LEFT JOIN staff ON staff.id = slots.staff_id "
            "LEFT JOIN branches ON branches.id = staff.branch_id "
            "WHERE client_id=? AND status='booked' AND slot_dt < ? "
            "ORDER BY slot_dt DESC LIMIT ?",
            (client_id, now_msk().strftime("%Y-%m-%d %H:%M"), limit),
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
