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
        "service_name": row["service_name"] if "service_name" in row.keys() else None,
        "no_show": bool(row["no_show"]) if "no_show" in row.keys() else False,
    }


def service_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "price": row["price"],
        "duration_min": row["duration_min"],
    }


def trainer_public_dict(trainer) -> dict:
    terms = terminology.terms_for(trainer["category_key"])
    return {
        "id": trainer["id"],
        "name": trainer["name"],
        "category": trainer["category"],
        "terms": terms,
        "services": [service_to_dict(s) for s in db.list_services(trainer["id"])],
    }


def create_app(bot, bot_token: str, bot_username: str) -> web.Application:
    app = web.Application()

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
                    "terms": terminology.terms_for(trainer["category_key"]),
                    "link": f"https://t.me/{bot_username}?start={trainer['id']}",
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
        if not name:
            return web.json_response({"error": "name required"}, status=400)
        db.register_trainer(user["id"], name, "")
        category_key = terminology.classify_category(category)
        db.set_trainer_category(user["id"], category, category_key)
        return web.json_response({"ok": True})

    @require_auth
    async def handle_provider_profile_update(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        if "name" in body:
            db.update_trainer_profile(user["id"], name=(body.get("name") or "").strip()[:80] or None)
        if "category" in body:
            category = (body.get("category") or "").strip()[:120]
            db.set_trainer_category(user["id"], category, terminology.classify_category(category))
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

    # ---------- расписание специалиста ----------

    @require_auth
    async def handle_provider_schedule(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        slots = db.list_all_upcoming(user["id"], limit=200)
        recent_past = db.list_recent_past_bookings(user["id"])
        return web.json_response({
            "slots": [slot_to_dict(s) | {"date": s["slot_dt"][:10]} for s in slots],
            "recent_past": [
                {"id": s["id"], "slot_dt": s["slot_dt"], "client_name": s["client_name"]}
                for s in recent_past
            ],
        })

    @require_auth
    async def handle_slot_add(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
        slot_dt = (body.get("slot_dt") or "").strip()
        try:
            datetime.strptime(slot_dt, "%Y-%m-%d %H:%M")
        except ValueError:
            return web.json_response({"error": "bad slot_dt"}, status=400)
        ok = db.add_slot(user["id"], slot_dt)
        if not ok:
            return web.json_response({"error": "already exists"}, status=409)
        return web.json_response({"ok": True})

    @require_auth
    async def handle_slot_add_recurring(request: web.Request, user: dict) -> web.Response:
        if not db.get_trainer(user["id"]):
            return web.json_response({"error": "not a provider"}, status=403)
        body = await request.json()
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
                if db.add_slot(user["id"], slot_dt):
                    added += 1
                else:
                    skipped += 1
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
        return web.json_response({
            "clients": [
                {"id": c["id"], "name": c["name"], "username": c["username"]}
                for c in clients
            ],
        })

    # ---------- клиентская сторона ----------

    @require_auth
    async def handle_client_home(request: web.Request, user: dict) -> web.Response:
        trainer_id = db.get_client_trainer(user["id"])
        trainer = db.get_trainer(trainer_id) if trainer_id else None
        if not trainer:
            return web.json_response({"error": "no trainer"}, status=404)

        days = []
        for day in db.list_free_days(trainer_id):
            slots = db.list_free_slots_for_day(trainer_id, day)
            if slots:
                days.append({"date": day, "label": fmt_day(day), "slots": [slot_to_dict(s) for s in slots]})

        result = trainer_public_dict(trainer)
        result["days"] = days
        return web.json_response(result)

    @require_auth
    async def handle_client_book(request: web.Request, user: dict) -> web.Response:
        body = await request.json()
        try:
            slot_id = int(body.get("slot_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad slot_id"}, status=400)
        service_id = body.get("service_id")
        service = db.get_service(int(service_id)) if service_id else None

        full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Без имени"
        username = user.get("username")

        ok = db.book_slot(
            slot_id, user["id"], full_name, username,
            service_id=service["id"] if service else None,
            service_name=service["name"] if service else None,
        )
        if not ok:
            return web.json_response({"error": "slot taken"}, status=409)

        slot = db.get_slot(slot_id)
        trainer = db.get_trainer(slot["trainer_id"])
        terms = terminology.terms_for(trainer["category_key"])
        service_line = f" — {esc(service['name'])}" if service else ""

        try:
            await bot.send_message(
                user["id"],
                f"🎉 Готово! Записал(а) тебя {terms['specialist_to']} "
                f"<b>{esc(trainer['name'])}</b> {terms['session_to']}{service_line} "
                f"на <b>{fmt_slot(slot['slot_dt'])}</b>.",
            )
        except Exception:
            logger.warning("Не удалось отправить подтверждение клиенту %s", user["id"])

        contact = f" (@{esc(username)})" if username else ""
        try:
            await bot.send_message(
                slot["trainer_id"],
                f"🔔 <b>Новая запись!</b>\n{esc(full_name)}{contact}{service_line} — "
                f"{fmt_slot(slot['slot_dt'])}",
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

        db.free_up_slot(slot_id)

        try:
            full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Клиент"
            await bot.send_message(
                slot["trainer_id"],
                f"⚠️ {esc(full_name)} отменил(а) запись на <b>{fmt_slot(slot['slot_dt'])}</b>",
            )
        except Exception:
            logger.warning("Не удалось уведомить специалиста об отмене")

        return web.json_response({"ok": True})

    @require_auth
    async def handle_client_my(request: web.Request, user: dict) -> web.Response:
        rows = db.list_client_bookings(user["id"])
        bookings = [
            {
                "id": r["id"], "slot_dt": r["slot_dt"], "trainer_name": r["trainer_name"],
                "service_name": r["service_name"] if "service_name" in r.keys() else None,
            }
            for r in rows
        ]
        return web.json_response({"bookings": bookings})

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
    app.router.add_get("/api/provider/schedule", handle_provider_schedule)
    app.router.add_post("/api/provider/slots/add", handle_slot_add)
    app.router.add_post("/api/provider/slots/add_recurring", handle_slot_add_recurring)
    app.router.add_post("/api/provider/slots/cancel", handle_slot_cancel)
    app.router.add_post("/api/provider/slots/noshow", handle_slot_noshow)
    app.router.add_get("/api/provider/clients", handle_clients_list)
    app.router.add_get("/api/client/home", handle_client_home)
    app.router.add_post("/api/client/book", handle_client_book)
    app.router.add_post("/api/client/cancel", handle_client_cancel)
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
