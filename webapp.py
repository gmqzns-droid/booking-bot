"""
Веб-часть Telegram Mini App: отдаёт статику (miniapp/) и JSON API.

Всё — и кабинет специалиста (услуги, расписание, клиенты), и запись клиента —
теперь живёт здесь, внутри мини-приложения. Чат используется только для
уведомлений (новая запись, отмена, напоминания) и одной кнопки входа в апп.

Работает в том же процессе, что и aiogram-бот (aiohttp-сервер поднимается
рядом с long polling, см. main() в bot.py).
"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import db
import terminology

logger = logging.getLogger(__name__)

MINIAPP_DIR = Path(__file__).parent / "miniapp"
INIT_DATA_MAX_AGE = 24 * 60 * 60  # сутки — старше не принимаем (защита от replay)
RECUR_WEEKS = 8

# Версия статики, вычисляется один раз при старте процесса (то есть меняется при каждом
# деплое). Подставляется в index.html как ?v=... к style.css/app.js — без этого браузер
# на телефоне может тихо продолжать использовать app.js, закэшированный ЕЩЁ ДО деплоя
# (index.html при этом честно грузится свежий благодаря no-store, а app.js остаётся
# старым — то есть страница выглядит "не грузится/не работает", хотя сервер отдаёт
# актуальный код). Меняющийся ?v= заставляет браузер запросить файл заново.
_ASSET_VERSION = str(int(time.time()))

DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def esc(text: str) -> str:
    return escape(text or "")


def fmt_slot(slot_dt: str) -> str:
    dt = datetime.strptime(slot_dt, "%Y-%m-%d %H:%M")
    return f"{DAYS_RU[dt.weekday()]}, {dt.day} {MONTHS_RU[dt.month]} в {dt.strftime('%H:%M')}"


def fmt_day(day: str) -> str:
    dt = datetime.strptime(day, "%Y-%m-%d")
    return f"{DAYS_RU[dt.weekday()]}, {dt.day} {MONTHS_RU[dt.month]}"


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Проверяет подпись Telegram.WebApp.initData по алгоритму из документации Telegram.
    Возвращает распарсенные поля (включая 'user' как dict) или None, если подпись неверна
    /данные протухли/initData вообще не пришёл."""
    if not init_data:
        logger.warning("validate_init_data: initData пустой")
        return None

    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        logger.warning("validate_init_data: нет поля hash. Поля: %s", sorted(data.keys()))
        return None

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    token = bot_token.strip()
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        logger.warning("validate_init_data: подпись не совпала. поля=%s", sorted(data.keys()))
        return None

    auth_date = data.get("auth_date")
    if auth_date and time.time() - int(auth_date) > INIT_DATA_MAX_AGE:
        logger.warning("validate_init_data: initData протух (auth_date=%s)", auth_date)
        return None

    if "user" in data:
        try:
            data["user"] = json.loads(data["user"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("validate_init_data: не смог распарсить поле user: %r", data["user"])
            data["user"] = None
    return data


def slot_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "time": row["slot_dt"][-5:],
        "status": row["status"],
        "client_name": row["client_name"] if "client_name" in row.keys() else None,
        "client_custom_name": row["client_custom_name"] if "client_custom_name" in row.keys() else None,
        "service_name": row["service_name"] if "service_name" in row.keys() else None,
        "staff_name": row["staff_name"] if "staff_name" in row.keys() else None,
        "no_show": bool(row["no_show"]) if "no_show" in row.keys() else False,
    }


def service_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "price": row["price"],
        "duration_min": row["duration_min"],
    }


def staff_to_dict(row) -> dict:
    return {"id": row["id"], "name": row["name"]}


def promo_discount_label(promo) -> str:
    if promo["discount_type"] == "free":
        return "Бесплатно"
    if promo["discount_type"] == "percent":
        return f"-{promo['discount_value']}%"
    if promo["discount_type"] == "fixed":
        return f"-{promo['discount_value']}₽"
    return promo["code"]


def promo_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "code": row["code"],
        "discount_type": row["discount_type"],
        "discount_value": row["discount_value"],
        "max_uses": row["max_uses"],
        "used_count": row["used_count"],
        "label": promo_discount_label(row),
    }


def trainer_public_dict(trainer) -> dict:
    terms = terminology.terms_for(trainer["category_key"])
    return {
        "id": trainer["id"],
        "name": trainer["name"],
        "category": trainer["category"],
        "address": trainer["address"] if "address" in trainer.keys() else None,
        "is_business": bool(trainer["is_business"]) if "is_business" in trainer.keys() else False,
        "cancel_min_hours": trainer["cancel_min_hours"] if "cancel_min_hours" in trainer.keys() else 0,
        "terms": terms,
        "services": [service_to_dict(s) for s in db.list_services(trainer["id"])],
        "staff": [staff_to_dict(s) for s in db.list_staff(trainer["id"])],
    }


def create_app(bot, bot_token: str, bot_username: str, mini_app_url: str = "") -> web.Application:
    app = web.Application()

    def open_app_kb() -> InlineKeyboardMarkup | None:
        """Та же кнопка входа, что и в bot.py — используется в уведомлениях из листа
        ожидания, чтобы клиент мог сразу открыть приложение и успеть забронировать."""
        if not mini_app_url:
            return None
        url = f"{mini_app_url.rstrip('/')}/miniapp/index.html?_t={int(time.time())}"
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть приложение", web_app=WebAppInfo(url=url))]]
        )

    async def notify_waitlist(trainer_id: int, staff_id: int):
        entries = db.pop_waitlist_for_staff(trainer_id, staff_id)
        if not entries:
            return
        kb = open_app_kb()
        for entry in entries:
            try:
                await bot.send_message(
                    entry["client_id"],
                    f"🔔 У <b>{esc(entry['staff_name'] or '')}</b> освободилось время — "
                    f"открывай приложение, чтобы успеть записаться!",
                    reply_markup=kb,
                )
            except Exception:
                logger.warning("Не удалось уведомить клиента %s из листа ожидания", entry["client_id"])

    def auth_user(request: web.Request):
        """Достаёт и проверяет initData из заголовка X-Telegram-Init-Data.
        Возвращает dict пользователя Telegram или None, если подпись неверна."""
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        parsed = validate_init_data(init_data, bot_token)
        if not parsed or not parsed.get("user"):
            logger.warning(
                "auth_user: проверка не прошла. len(init_data)=%s, начало=%r",
                len(init_data or ""), (init_data or "")[:60],
            )
            return None
        return parsed["user"]

    def require_auth(handler):
        async def wrapped(request: web.Request):
            user = auth_user(request)
            if not user:
                return web.json_response({"error": "unauthorized"}, status=401)
            return await handler(request, user)
        return wrapped

    # ---------- whoami / профиль ----------

    @require_auth
    async def handle_whoami(request: web.Request, user: dict) -> web.Response:
        uid = user["id"]
        trainer = db.get_trainer(uid)
        if trainer:
            return web.json_response({
                "role": "provider",
                "provider": {
                    "id": trainer["id"],
                    "name": trainer["name"],
                    "category": trainer["category"],
                    "address": trainer["address"] if "address" in trainer.keys() else None,
                    "is_business": bool(trainer["is_business"]),
                    "cancel_min_hours": trainer["cancel_min_hours"] if "cancel_min_hours" in trainer.keys() else 0,
                    "terms": terminology.terms_for(trainer["category_key"]),
                    "link": f"https://t.me/{bot_username}?start={trainer['id']}",
                    "staff": [staff_to_dict(s) for s in db.list_staff(trainer["id"])],
                },
            })
        client_trainer_id = db.get_client_trainer(uid)
        if client_trainer_id and db.get_trainer(client_trainer_id):
            return web.json_response({"role": "client"})
        return web.json_response({"role": "guest"})

    @require_auth
    async def handle_provider_register(request: web.Request, user: dict) -> web.Response:
        body = await request.json()
        name = (body.get("name") or "").strip()[:80]
        category = (body.get("category") or "").strip()[:120]
        is_business = bool(body.get("is_business"))
        if not name:
            return web.json_response({"error": "name required"}, status=400)
        db.register_trainer(user["id"], name, "")
        category_key = terminology.classify_category(category)
        db.set_trainer_category(user["id"], category, category_key)
        db.set_trainer_is_business(user["id"], is_business)
        if not is_business:
            # Соло-специалист всегда представлен ровно одним "сотрудником" (собой) —
            # так и расписание, и запись клиента работают по единой staff_id-модели,
            # но без отдельного экрана выбора мастера в UI.
            db.add_staff(user["id"], name)
        return web.json_response({"ok": True})

    @require_auth
    async def handle_provider_profile_update(request: web.Request, user: dict) -> web.Response:
        trainer = db.get_trainer(user["id"])
        if not trainer:
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        if "name" in body:
            db.update_trainer_profile(user["id"], name=(body.get("name") or "").strip()[:80] or None)
        if "category" in body:
            category = (body.get("category") or "").strip()[:120]
            db.set_trainer_category(user["id"], category, terminology.classify_category(category))
        if "address" in body:
            address = (body.get("address") or "").strip()[:200]
            db.set_trainer_address(user["id"], address or None)
        if "is_business" in body:
            want_business = bool(body.get("is_business"))
            if not want_business and db.count_staff(user["id"]) > 1:
                return web.json_response({"error": "too many staff"}, status=409)
            db.set_trainer_is_business(user["id"], want_business)
        if "cancel_min_hours" in body:
            try:
                hours = int(body.get("cancel_min_hours") or 0)
            except (TypeError, ValueError):
                return web.json_response({"error": "bad cancel_min_hours"}, status=400)
            if hours < 0 or hours > 168:
                return web.json_response({"error": "bad cancel_min_hours"}, status=400)
            db.set_trainer_cancel_min_hours(user["id"], hours)
        return web.json_response({"ok": True})

    # ---------- услуги ----------

    @require_auth
    async def handle_services_list(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        services = [service_to_dict(s) for s in db.list_services(user["id"])]
        return web.json_response({"services": services})

    @require_auth
    async def handle_services_add(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        name = (body.get("name") or "").strip()[:80]
        if not name:
            return web.json_response({"error": "name required"}, status=400)
        price = body.get("price")
        duration_min = body.get("duration_min")
        sid = db.add_service(user["id"], name, price, duration_min)
        return web.json_response({"ok": True, "id": sid})

    @require_auth
    async def handle_services_update(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        service = db.get_service(int(body.get("id", 0)))
        if not service or service["trainer_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)
        kwargs = {}
        if "name" in body:
            kwargs["name"] = (body.get("name") or "").strip()[:80]
        if "price" in body:
            kwargs["price"] = body.get("price")
        if "duration_min" in body:
            kwargs["duration_min"] = body.get("duration_min")
        db.update_service(service["id"], **kwargs)
        return web.json_response({"ok": True})

    @require_auth
    async def handle_services_delete(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        service = db.get_service(int(body.get("id", 0)))
        if not service or service["trainer_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)
        db.delete_service(service["id"])
        return web.json_response({"ok": True})

    # ---------- сотрудники (только для бизнес-аккаунтов) ----------

    @require_auth
    async def handle_staff_list(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        staff = [staff_to_dict(s) for s in db.list_staff(user["id"])]
        return web.json_response({"staff": staff})

    @require_auth
    async def handle_staff_add(request: web.Request, user: dict) -> web.Response:
        trainer = db.get_trainer(user["id"])
        if not trainer or not trainer["is_business"]:
            return web.json_response({"error": "not a business"}, status=403)
        body = await request.json()
        name = (body.get("name") or "").strip()[:80]
        if not name:
            return web.json_response({"error": "name required"}, status=400)
        sid = db.add_staff(user["id"], name)
        return web.json_response({"ok": True, "id": sid})

    @require_auth
    async def handle_staff_update(request: web.Request, user: dict) -> web.Response:
        trainer = db.get_trainer(user["id"])
        if not trainer or not trainer["is_business"]:
            return web.json_response({"error": "not a business"}, status=403)
        body = await request.json()
        staff = db.get_staff(int(body.get("id", 0)))
        if not staff or staff["business_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)
        name = (body.get("name") or "").strip()[:80]
        if not name:
            return web.json_response({"error": "name required"}, status=400)
        db.update_staff(staff["id"], name=name)
        return web.json_response({"ok": True})

    @require_auth
    async def handle_staff_delete(request: web.Request, user: dict) -> web.Response:
        trainer = db.get_trainer(user["id"])
        if not trainer or not trainer["is_business"]:
            return web.json_response({"error": "not a business"}, status=403)
        body = await request.json()
        staff = db.get_staff(int(body.get("id", 0)))
        if not staff or staff["business_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)
        if db.count_staff(user["id"]) <= 1:
            return web.json_response({"error": "last staff"}, status=409)
        db.delete_staff(staff["id"])
        return web.json_response({"ok": True})

    # ---------- расписание специалиста (в разрезе конкретного сотрудника) ----------

    def _staff_for_provider(user_id: int, raw_staff_id):
        """Проверяет, что staff_id передан и принадлежит бизнесу текущего провайдера.
        Возвращает staff-строку или None."""
        try:
            staff_id = int(raw_staff_id)
        except (TypeError, ValueError):
            return None
        staff = db.get_staff(staff_id)
        if not staff or staff["business_id"] != user_id:
            return None
        return staff

    @require_auth
    async def handle_provider_schedule(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        staff = _staff_for_provider(user["id"], request.query.get("staff_id"))
        if not staff:
            return web.json_response({"error": "bad staff_id"}, status=400)
        slots = db.list_all_upcoming(user["id"], staff["id"], limit=200)
        recent_past = db.list_recent_past_bookings(user["id"], staff["id"])
        return web.json_response({
            "slots": [slot_to_dict(s) | {"date": s["slot_dt"][:10]} for s in slots],
            "recent_past": [
                {
                    "id": s["id"], "slot_dt": s["slot_dt"], "client_name": s["client_name"],
                    "client_custom_name": s["client_custom_name"] if "client_custom_name" in s.keys() else None,
                }
                for s in recent_past
            ],
        })

    @require_auth
    async def handle_slot_add(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        staff = _staff_for_provider(user["id"], body.get("staff_id"))
        if not staff:
            return web.json_response({"error": "bad staff_id"}, status=400)
        slot_dt = (body.get("slot_dt") or "").strip()
        try:
            datetime.strptime(slot_dt, "%Y-%m-%d %H:%M")
        except ValueError:
            return web.json_response({"error": "bad slot_dt"}, status=400)
        ok = db.add_slot(user["id"], staff["id"], staff["name"], slot_dt)
        if not ok:
            return web.json_response({"error": "already exists"}, status=409)
        await notify_waitlist(user["id"], staff["id"])
        return web.json_response({"ok": True})

    @require_auth
    async def handle_slot_add_recurring(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        staff = _staff_for_provider(user["id"], body.get("staff_id"))
        if not staff:
            return web.json_response({"error": "bad staff_id"}, status=400)
        weekdays = body.get("weekdays") or []
        try:
            hh, mm = int(body.get("hh")), int(body.get("mm"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad time"}, status=400)
        weekdays = [int(w) for w in weekdays if isinstance(w, (int, str)) and 0 <= int(w) <= 6]
        if not weekdays:
            return web.json_response({"error": "no weekdays"}, status=400)

        today = db.now_msk().date()
        added = skipped = 0
        for wd in weekdays:
            delta = (wd - today.weekday()) % 7
            base = today + timedelta(days=delta)
            for week in range(RECUR_WEEKS):
                d = base + timedelta(weeks=week)
                slot_dt = f"{d:%Y-%m-%d} {hh:02d}:{mm:02d}"
                if db.add_slot(user["id"], staff["id"], staff["name"], slot_dt):
                    added += 1
                else:
                    skipped += 1
        if added:
            await notify_waitlist(user["id"], staff["id"])
        return web.json_response({"ok": True, "added": added, "skipped": skipped})

    @require_auth
    async def handle_slot_cancel(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        slot = db.get_slot(int(body.get("slot_id", 0)))
        if not slot or slot["trainer_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)
        was_booked = slot["status"] == "booked"
        client_id = slot["client_id"]
        db.cancel_slot(slot["id"])
        if was_booked and client_id:
            try:
                await bot.send_message(
                    client_id,
                    f"⚠️ Специалист отменил запись на <b>{fmt_slot(slot['slot_dt'])}</b>. "
                    f"Извини за неудобство!",
                )
            except Exception:
                logger.warning("Не удалось уведомить клиента %s об отмене", client_id)
        return web.json_response({"ok": True})

    @require_auth
    async def handle_slot_reschedule(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        try:
            slot_id = int(body.get("slot_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad slot_id"}, status=400)
        old = db.get_slot(slot_id)
        if not old or old["trainer_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)
        if old["status"] != "booked":
            return web.json_response({"error": "not booked"}, status=400)

        new_slot_id = body.get("new_slot_id")
        if new_slot_id:
            new = db.get_slot(int(new_slot_id))
            if not new or new["trainer_id"] != user["id"] or new["staff_id"] != old["staff_id"]:
                return web.json_response({"error": "bad new_slot_id"}, status=400)
            if new["status"] != "free":
                return web.json_response({"error": "slot taken"}, status=409)
            new_id = new["id"]
        else:
            new_slot_dt = (body.get("new_slot_dt") or "").strip()
            try:
                datetime.strptime(new_slot_dt, "%Y-%m-%d %H:%M")
            except ValueError:
                return web.json_response({"error": "bad new_slot_dt"}, status=400)
            existing = db.get_slot_by_dt(old["trainer_id"], old["staff_id"], new_slot_dt)
            if existing:
                if existing["id"] == old["id"]:
                    return web.json_response({"ok": True})
                if existing["status"] != "free":
                    return web.json_response({"error": "slot taken"}, status=409)
                new_id = existing["id"]
            else:
                db.add_slot(old["trainer_id"], old["staff_id"], old["staff_name"], new_slot_dt)
                created = db.get_slot_by_dt(old["trainer_id"], old["staff_id"], new_slot_dt)
                new_id = created["id"]

        new_row = db.get_slot(new_id)
        old_service = db.get_service(old["service_id"]) if "service_id" in old.keys() and old["service_id"] else None
        if old_service and old_service["duration_min"]:
            conflict = db.find_overlapping_booked_slot(
                old["trainer_id"], old["staff_id"], new_row["slot_dt"], old_service["duration_min"],
                exclude_slot_id=old["id"],
            )
            if conflict:
                return web.json_response({"error": "slot_conflict"}, status=409)

        old_dt, client_id = old["slot_dt"], old["client_id"]
        ok = db.reschedule_slot(old["id"], new_id)
        if not ok:
            return web.json_response({"error": "slot taken"}, status=409)

        new_slot = db.get_slot(new_id)
        if client_id:
            try:
                await bot.send_message(
                    client_id,
                    f"🔄 Специалист перенёс твою запись с <b>{fmt_slot(old_dt)}</b> "
                    f"на <b>{fmt_slot(new_slot['slot_dt'])}</b>.",
                )
            except Exception:
                logger.warning("Не удалось уведомить клиента %s о переносе", client_id)
        await notify_waitlist(old["trainer_id"], old["staff_id"])
        return web.json_response({"ok": True})

    @require_auth
    async def handle_schedule_close_range(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        staff = _staff_for_provider(user["id"], body.get("staff_id"))
        if not staff:
            return web.json_response({"error": "bad staff_id"}, status=400)
        start_date = (body.get("start_date") or "").strip()
        end_date = (body.get("end_date") or "").strip()
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            return web.json_response({"error": "bad dates"}, status=400)
        if end_date < start_date:
            return web.json_response({"error": "bad range"}, status=400)

        slots = db.list_slots_in_range(user["id"], staff["id"], start_date, end_date)
        freed = 0
        cancelled_bookings = 0
        for slot in slots:
            was_booked = slot["status"] == "booked"
            client_id = slot["client_id"]
            db.cancel_slot(slot["id"])
            if was_booked:
                cancelled_bookings += 1
                if client_id:
                    try:
                        await bot.send_message(
                            client_id,
                            f"⚠️ Специалист закрыл(а) этот период — запись на "
                            f"<b>{fmt_slot(slot['slot_dt'])}</b> отменена. Извини за неудобство!",
                        )
                    except Exception:
                        logger.warning("Не удалось уведомить клиента %s о закрытии периода", client_id)
            else:
                freed += 1
        return web.json_response({"ok": True, "freed": freed, "cancelled_bookings": cancelled_bookings})

    @require_auth
    async def handle_slot_noshow(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        slot = db.get_slot(int(body.get("slot_id", 0)))
        if not slot or slot["trainer_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)
        db.mark_no_show(slot["id"])
        return web.json_response({"ok": True})

    @require_auth
    async def handle_clients_list(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        clients = db.list_clients(user["id"])
        next_by_client = db.next_bookings_by_client(user["id"])
        trainer_is_business = bool(db.get_trainer(user["id"])["is_business"])

        def next_booking_dict(slot):
            if not slot:
                return None
            return {
                "slot_id": slot["id"],
                "slot_dt": slot["slot_dt"],
                "service_name": slot["service_name"],
                "staff_name": slot["staff_name"] if trainer_is_business else None,
                "promo_code": slot["promo_code"],
                "discount_label": slot["discount_label"],
                "client_custom_name": slot["client_custom_name"] if "client_custom_name" in slot.keys() else None,
            }

        return web.json_response({
            "clients": [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "custom_name": c["custom_name"] if "custom_name" in c.keys() else None,
                    "username": c["username"],
                    "blocked": bool(c["blocked"]),
                    "next_booking": next_booking_dict(next_by_client.get(c["id"])),
                }
                for c in clients
            ],
        })

    @require_auth
    async def handle_client_block(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        try:
            client_id = int(body.get("client_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad client_id"}, status=400)
        blocked = bool(body.get("blocked"))
        ok = db.set_client_blocked(user["id"], client_id, blocked)
        if not ok:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"ok": True})

    @require_auth
    async def handle_provider_broadcast(request: web.Request, user: dict) -> web.Response:
        trainer = db.get_trainer(user["id"])
        if not trainer:
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text:
            return web.json_response({"error": "empty text"}, status=400)
        if len(text) > 1000:
            return web.json_response({"error": "too long"}, status=400)

        clients = [c for c in db.list_clients(user["id"]) if not c["blocked"]]
        message = f"📢 <b>{esc(trainer['name'])}</b>:\n{esc(text)}"
        sent = 0
        failed = 0
        for c in clients:
            try:
                await bot.send_message(c["id"], message)
                sent += 1
            except Exception:
                failed += 1
        return web.json_response({"ok": True, "sent": sent, "failed": failed, "total": len(clients)})

    @require_auth
    async def handle_provider_stats(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        staff_id_raw = request.query.get("staff_id")
        staff_id = None
        if staff_id_raw:
            staff = _staff_for_provider(user["id"], staff_id_raw)
            if not staff:
                return web.json_response({"error": "bad staff_id"}, status=400)
            staff_id = staff["id"]
        period = request.query.get("period", "week")
        days = 30 if period == "month" else 7
        return web.json_response(db.trainer_stats(user["id"], staff_id, days))

    @require_auth
    async def handle_provider_reviews(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        summary = db.trainer_rating_summary(user["id"])
        reviews = [
            {
                "id": r["id"], "rating": r["rating"], "comment": r["comment"],
                "client_name": r["client_name"], "staff_name": r["staff_name"],
                "created_at": r["created_at"],
            }
            for r in db.list_reviews_for_trainer(user["id"])
        ]
        return web.json_response({"summary": summary, "reviews": reviews})

    # ---------- промокоды ----------

    @require_auth
    async def handle_promos_list(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        promos = [promo_to_dict(p) for p in db.list_promos(user["id"])]
        return web.json_response({"promos": promos})

    @require_auth
    async def handle_promos_add(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        code = (body.get("code") or "").strip().upper()[:20]
        discount_type = body.get("discount_type")
        if not code or discount_type not in ("percent", "fixed", "free"):
            return web.json_response({"error": "bad input"}, status=400)
        discount_value = None
        if discount_type in ("percent", "fixed"):
            try:
                discount_value = int(body.get("discount_value"))
            except (TypeError, ValueError):
                return web.json_response({"error": "bad discount_value"}, status=400)
            if discount_type == "percent" and not (1 <= discount_value <= 100):
                return web.json_response({"error": "bad discount_value"}, status=400)
            if discount_type == "fixed" and discount_value <= 0:
                return web.json_response({"error": "bad discount_value"}, status=400)
        max_uses = body.get("max_uses")
        try:
            max_uses = int(max_uses) if max_uses not in (None, "") else None
        except (TypeError, ValueError):
            return web.json_response({"error": "bad max_uses"}, status=400)
        promo_id = db.add_promo(user["id"], code, discount_type, discount_value, max_uses)
        if promo_id is None:
            return web.json_response({"error": "code exists"}, status=409)
        return web.json_response({"ok": True, "id": promo_id})

    @require_auth
    async def handle_promos_delete(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        promo = db.get_promo(int(body.get("id", 0)))
        if not promo or promo["trainer_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)
        db.delete_promo(promo["id"])
        return web.json_response({"ok": True})

    # ---------- клиентская сторона ----------

    @require_auth
    async def handle_client_home(request: web.Request, user: dict) -> web.Response:
        trainer_id = db.get_client_trainer(user["id"])
        trainer = db.get_trainer(trainer_id) if trainer_id else None
        if not trainer:
            return web.json_response({"error": "no trainer"}, status=404)
        # Дни/слоты сюда не включаем — они запрашиваются отдельно для конкретного
        # сотрудника через /api/client/staff_schedule, после того как клиент его выберет
        # (при одном сотруднике — соло-специалист — фронтенд выберет его сам, без показа выбора).
        data = trainer_public_dict(trainer)
        data["own_name"] = db.get_client_custom_name(user["id"])
        return web.json_response(data)

    @require_auth
    async def handle_client_staff_schedule(request: web.Request, user: dict) -> web.Response:
        trainer_id = db.get_client_trainer(user["id"])
        trainer = db.get_trainer(trainer_id) if trainer_id else None
        if not trainer:
            return web.json_response({"error": "no trainer"}, status=404)
        try:
            staff_id = int(request.query.get("staff_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad staff_id"}, status=400)
        staff = db.get_staff(staff_id)
        if not staff or staff["business_id"] != trainer_id:
            return web.json_response({"error": "not found"}, status=404)

        days = []
        for day in db.list_free_days(trainer_id, staff_id):
            slots = db.list_free_slots_for_day(trainer_id, staff_id, day)
            if slots:
                days.append({"date": day, "label": fmt_day(day), "slots": [slot_to_dict(s) for s in slots]})
        on_waitlist = bool(db.get_waitlist_entry(trainer_id, staff_id, user["id"]))
        return web.json_response({"days": days, "on_waitlist": on_waitlist})

    @require_auth
    async def handle_waitlist_join(request: web.Request, user: dict) -> web.Response:
        trainer_id = db.get_client_trainer(user["id"])
        trainer = db.get_trainer(trainer_id) if trainer_id else None
        if not trainer:
            return web.json_response({"error": "no trainer"}, status=404)
        if db.is_client_blocked(trainer_id, user["id"]):
            return web.json_response({"error": "blocked"}, status=403)
        body = await request.json()
        try:
            staff_id = int(body.get("staff_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad staff_id"}, status=400)
        staff = db.get_staff(staff_id)
        if not staff or staff["business_id"] != trainer_id:
            return web.json_response({"error": "not found"}, status=404)
        service_id = body.get("service_id")
        service = db.get_service(int(service_id)) if service_id else None
        full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Без имени"
        db.join_waitlist(
            trainer_id, staff["id"], staff["name"], user["id"], full_name, user.get("username"),
            service_id=service["id"] if service else None,
            service_name=service["name"] if service else None,
        )
        return web.json_response({"ok": True})

    @require_auth
    async def handle_waitlist_leave(request: web.Request, user: dict) -> web.Response:
        trainer_id = db.get_client_trainer(user["id"])
        if not trainer_id:
            return web.json_response({"error": "no trainer"}, status=404)
        body = await request.json()
        try:
            staff_id = int(body.get("staff_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad staff_id"}, status=400)
        db.leave_waitlist(trainer_id, staff_id, user["id"])
        return web.json_response({"ok": True})

    @require_auth
    async def handle_client_book(request: web.Request, user: dict) -> web.Response:
        body = await request.json()
        try:
            slot_id = int(body.get("slot_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad slot_id"}, status=400)
        service_id = body.get("service_id")
        service = db.get_service(int(service_id)) if service_id else None

        slot_pre = db.get_slot(slot_id)
        if not slot_pre:
            return web.json_response({"error": "not found"}, status=404)
        if db.is_client_blocked(slot_pre["trainer_id"], user["id"]):
            return web.json_response({"error": "blocked"}, status=403)

        promo = None
        promo_code_input = (body.get("promo_code") or "").strip()
        if promo_code_input:
            promo = db.get_active_promo_by_code(slot_pre["trainer_id"], promo_code_input)
            if not promo:
                return web.json_response({"error": "promo_invalid"}, status=400)
            if promo["max_uses"] is not None and promo["used_count"] >= promo["max_uses"]:
                return web.json_response({"error": "promo_exhausted"}, status=409)
            if db.has_client_used_promo(promo["id"], user["id"]):
                return web.json_response({"error": "promo_used"}, status=409)

        if service and service["duration_min"]:
            conflict = db.find_overlapping_booked_slot(
                slot_pre["trainer_id"], slot_pre["staff_id"], slot_pre["slot_dt"], service["duration_min"],
            )
            if conflict:
                return web.json_response({"error": "slot_conflict"}, status=409)

        full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Без имени"
        username = user.get("username")
        custom_name = (body.get("client_name") or "").strip()[:80] or None
        if not custom_name:
            return web.json_response({"error": "name_required"}, status=400)

        ok = db.book_slot(
            slot_id, user["id"], full_name, username,
            service_id=service["id"] if service else None,
            service_name=service["name"] if service else None,
            client_custom_name=custom_name,
        )
        if not ok:
            return web.json_response({"error": "slot taken"}, status=409)
        db.set_client_custom_name(user["id"], custom_name)

        discount_label = None
        if promo:
            db.redeem_promo(promo["id"], user["id"], slot_id)
            discount_label = promo_discount_label(promo)
            db.set_slot_promo(slot_id, promo["code"], discount_label)

        slot = db.get_slot(slot_id)
        trainer = db.get_trainer(slot["trainer_id"])
        if "staff_id" in slot.keys() and slot["staff_id"]:
            db.leave_waitlist(slot["trainer_id"], slot["staff_id"], user["id"])
        terms = terminology.terms_for(trainer["category_key"])
        service_line = f" — {esc(service['name'])}" if service else ""
        promo_line = f" 🏷 промокод «{esc(promo['code'])}» ({esc(discount_label)})" if promo else ""
        staff_name = slot["staff_name"] if "staff_name" in slot.keys() and slot["staff_name"] else trainer["name"]
        # В режиме компании уточняем и бизнес, и конкретного сотрудника; у соло-специалиста
        # staff_name совпадает с его собственным именем, поэтому просто одно имя.
        who_line = (
            f"<b>{esc(staff_name)}</b> ({esc(trainer['name'])})"
            if trainer["is_business"] else f"<b>{esc(staff_name)}</b>"
        )

        try:
            await bot.send_message(
                user["id"],
                f"🎉 Готово! Записал(а) тебя {terms['specialist_to']} "
                f"{who_line} {terms['session_to']}{service_line} "
                f"на <b>{fmt_slot(slot['slot_dt'])}</b>.{promo_line}",
            )
        except Exception:
            logger.warning("Не удалось отправить подтверждение клиенту %s", user["id"])

        name_line = f"{esc(custom_name)} (тг: {esc(full_name)})" if custom_name != full_name else esc(full_name)
        contact = f" (@{esc(username)})" if username else ""
        staff_line = f" · к {esc(staff_name)}" if trainer["is_business"] else ""
        try:
            await bot.send_message(
                slot["trainer_id"],
                f"🔔 <b>Новая запись!</b>\n{name_line}{contact}{service_line}{staff_line} — "
                f"{fmt_slot(slot['slot_dt'])}{promo_line}",
            )
        except Exception:
            logger.warning("Не удалось уведомить специалиста")

        return web.json_response({"ok": True})

    @require_auth
    async def handle_client_cancel(request: web.Request, user: dict) -> web.Response:
        body = await request.json()
        try:
            slot_id = int(body.get("slot_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad slot_id"}, status=400)

        slot = db.get_slot(slot_id)
        if not slot or slot["client_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)

        trainer = db.get_trainer(slot["trainer_id"])
        min_hours = trainer["cancel_min_hours"] if trainer and "cancel_min_hours" in trainer.keys() else 0
        if min_hours:
            slot_time = datetime.strptime(slot["slot_dt"], "%Y-%m-%d %H:%M")
            if slot_time - db.now_msk().replace(tzinfo=None) < timedelta(hours=min_hours):
                return web.json_response({"error": "cancel_too_late", "min_hours": min_hours}, status=403)

        db.free_up_slot(slot_id)

        try:
            full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Клиент"
            await bot.send_message(
                slot["trainer_id"],
                f"⚠️ {esc(full_name)} отменил(а) запись на <b>{fmt_slot(slot['slot_dt'])}</b>",
            )
        except Exception:
            logger.warning("Не удалось уведомить специалиста об отмене")

        if "staff_id" in slot.keys() and slot["staff_id"]:
            await notify_waitlist(slot["trainer_id"], slot["staff_id"])

        return web.json_response({"ok": True})

    @require_auth
    async def handle_client_reschedule(request: web.Request, user: dict) -> web.Response:
        body = await request.json()
        try:
            slot_id = int(body.get("slot_id"))
            new_slot_id = int(body.get("new_slot_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad slot_id"}, status=400)

        old = db.get_slot(slot_id)
        if not old or old["client_id"] != user["id"]:
            return web.json_response({"error": "not found"}, status=404)
        new = db.get_slot(new_slot_id)
        if not new or new["trainer_id"] != old["trainer_id"] or new["staff_id"] != old["staff_id"]:
            return web.json_response({"error": "bad new_slot_id"}, status=400)
        if new["status"] != "free":
            return web.json_response({"error": "slot taken"}, status=409)

        trainer = db.get_trainer(old["trainer_id"])
        min_hours = trainer["cancel_min_hours"] if trainer and "cancel_min_hours" in trainer.keys() else 0
        if min_hours:
            old_time = datetime.strptime(old["slot_dt"], "%Y-%m-%d %H:%M")
            if old_time - db.now_msk().replace(tzinfo=None) < timedelta(hours=min_hours):
                return web.json_response({"error": "cancel_too_late", "min_hours": min_hours}, status=403)

        old_service = db.get_service(old["service_id"]) if "service_id" in old.keys() and old["service_id"] else None
        if old_service and old_service["duration_min"]:
            conflict = db.find_overlapping_booked_slot(
                old["trainer_id"], old["staff_id"], new["slot_dt"], old_service["duration_min"],
                exclude_slot_id=old["id"],
            )
            if conflict:
                return web.json_response({"error": "slot_conflict"}, status=409)

        old_dt = old["slot_dt"]
        ok = db.reschedule_slot(slot_id, new_slot_id)
        if not ok:
            return web.json_response({"error": "slot taken"}, status=409)

        try:
            full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Клиент"
            await bot.send_message(
                old["trainer_id"],
                f"🔄 {esc(full_name)} перенёс(ла) запись с <b>{fmt_slot(old_dt)}</b> "
                f"на <b>{fmt_slot(new['slot_dt'])}</b>",
            )
        except Exception:
            logger.warning("Не удалось уведомить специалиста о переносе")

        if old["staff_id"]:
            await notify_waitlist(old["trainer_id"], old["staff_id"])

        return web.json_response({"ok": True})

    def _booking_to_dict(r) -> dict:
        return {
            "id": r["id"], "slot_dt": r["slot_dt"], "trainer_name": r["trainer_name"],
            "trainer_address": r["trainer_address"] if "trainer_address" in r.keys() else None,
            "service_id": r["service_id"] if "service_id" in r.keys() else None,
            "service_name": r["service_name"] if "service_name" in r.keys() else None,
            "staff_id": r["staff_id"] if "staff_id" in r.keys() else None,
            "staff_name": r["staff_name"] if "staff_name" in r.keys() else None,
            "discount_label": r["discount_label"] if "discount_label" in r.keys() else None,
        }

    @require_auth
    async def handle_client_my(request: web.Request, user: dict) -> web.Response:
        bookings = [_booking_to_dict(r) for r in db.list_client_bookings(user["id"])]
        past = [_booking_to_dict(r) for r in db.list_client_past_bookings(user["id"])]
        return web.json_response({"bookings": bookings, "past": past})

    # ---------- диагностика (временно, можно снести после стабилизации) ----------

    async def handle_debug(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        logger.warning(
            "miniapp debug: tg_global=%s webapp_global=%s platform=%r version=%r "
            "initData_len=%s hash=%r search=%r ua=%r",
            body.get("telegram_global"), body.get("webapp_global"),
            body.get("platform"), body.get("version"), body.get("initData_len"),
            body.get("location_hash"), body.get("location_search"),
            request.headers.get("User-Agent"),
        )
        return web.json_response({"ok": True})

    # ---------- маршруты ----------

    app.router.add_post("/api/whoami", handle_whoami)
    app.router.add_post("/api/provider/register", handle_provider_register)
    app.router.add_post("/api/provider/profile", handle_provider_profile_update)
    app.router.add_get("/api/provider/services", handle_services_list)
    app.router.add_post("/api/provider/services/add", handle_services_add)
    app.router.add_post("/api/provider/services/update", handle_services_update)
    app.router.add_post("/api/provider/services/delete", handle_services_delete)
    app.router.add_get("/api/provider/staff", handle_staff_list)
    app.router.add_post("/api/provider/staff/add", handle_staff_add)
    app.router.add_post("/api/provider/staff/update", handle_staff_update)
    app.router.add_post("/api/provider/staff/delete", handle_staff_delete)
    app.router.add_get("/api/provider/schedule", handle_provider_schedule)
    app.router.add_post("/api/provider/slots/add", handle_slot_add)
    app.router.add_post("/api/provider/slots/add_recurring", handle_slot_add_recurring)
    app.router.add_post("/api/provider/slots/cancel", handle_slot_cancel)
    app.router.add_post("/api/provider/slots/reschedule", handle_slot_reschedule)
    app.router.add_post("/api/provider/schedule/close_range", handle_schedule_close_range)
    app.router.add_post("/api/provider/slots/noshow", handle_slot_noshow)
    app.router.add_get("/api/provider/clients", handle_clients_list)
    app.router.add_post("/api/provider/clients/block", handle_client_block)
    app.router.add_post("/api/provider/broadcast", handle_provider_broadcast)
    app.router.add_get("/api/provider/stats", handle_provider_stats)
    app.router.add_get("/api/provider/reviews", handle_provider_reviews)
    app.router.add_get("/api/provider/promos", handle_promos_list)
    app.router.add_post("/api/provider/promos/add", handle_promos_add)
    app.router.add_post("/api/provider/promos/delete", handle_promos_delete)
    app.router.add_get("/api/client/home", handle_client_home)
    app.router.add_get("/api/client/staff_schedule", handle_client_staff_schedule)
    app.router.add_post("/api/client/waitlist/join", handle_waitlist_join)
    app.router.add_post("/api/client/waitlist/leave", handle_waitlist_leave)
    app.router.add_post("/api/client/book", handle_client_book)
    app.router.add_post("/api/client/cancel", handle_client_cancel)
    app.router.add_post("/api/client/reschedule", handle_client_reschedule)
    app.router.add_get("/api/client/my", handle_client_my)
    app.router.add_post("/api/debug", handle_debug)

    async def handle_index(request: web.Request) -> web.Response:
        """index.html отдаём вручную (не через add_static) с no-store — чтобы Telegram/WebKit
        никогда не показывал закэшированную версию страницы без свежих initData-параметров.
        Заодно подставляем ?v=<версия деплоя> в ссылки на style.css/app.js, чтобы браузер
        не мог тихо взять их из своего кэша от предыдущего деплоя (см. _ASSET_VERSION выше)."""
        text = (MINIAPP_DIR / "index.html").read_text(encoding="utf-8")
        text = text.replace('href="style.css"', f'href="style.css?v={_ASSET_VERSION}"')
        text = text.replace('src="app.js"', f'src="app.js?v={_ASSET_VERSION}"')
        return web.Response(
            text=text, content_type="text/html",
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    def make_static_asset_handler(filename: str, content_type: str):
        async def handler(request: web.Request) -> web.Response:
            """style.css/app.js тоже отдаём с no-store — на случай, если браузер всё же
            зайдёт на них напрямую (не через свежий index.html), чтобы не получить старую
            закэшированную версию в обход cache-busting выше."""
            text = (MINIAPP_DIR / filename).read_text(encoding="utf-8")
            return web.Response(
                text=text, content_type=content_type,
                headers={"Cache-Control": "no-store, must-revalidate"},
            )
        return handler

    app.router.add_get("/miniapp/index.html", handle_index)
    app.router.add_get("/miniapp/style.css", make_static_asset_handler("style.css", "text/css"))
    app.router.add_get("/miniapp/app.js", make_static_asset_handler("app.js", "application/javascript"))
    app.router.add_static("/miniapp/", path=MINIAPP_DIR, show_index=False)

    async def handle_root(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", handle_root)

    return app
