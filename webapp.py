"""
Веб-часть для Telegram Mini App: отдаёт статику мини-приложения (miniapp/)
и JSON API для записи клиента к тренеру через календарь вместо кнопок бота.

Работает в том же процессе, что и aiogram-бот (aiohttp-сервер поднимается
рядом с long polling, см. main() в bot.py).
"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

import db

logger = logging.getLogger(__name__)

MINIAPP_DIR = Path(__file__).parent / "miniapp"
INIT_DATA_MAX_AGE = 24 * 60 * 60  # сутки — старше не принимаем (защита от replay)

DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def esc(text: str) -> str:
    return escape(text or "")


def fmt_slot(slot_dt: str) -> str:
    dt = datetime.strptime(slot_dt, "%Y-%m-%d %H:%M")
    return f"{DAYS_RU[dt.weekday()]}, {dt.day} {MONTHS_RU[dt.month]} в {dt.strftime('%H:%M')}"


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Проверяет подпись Telegram.WebApp.initData по алгоритму из документации Telegram.
    Возвращает распарсенные поля (включая 'user' как dict) или None, если подпись неверна
    /данные протухли.

    Временно логирует ТОЧНУЮ причину отказа на каждом шаге — нужно, чтобы поймать
    баг с 401 в проде (offline-тесты самого алгоритма проходят чисто, значит дело
    либо в токене/окружении, либо в форме initData, которую реально шлёт Telegram)."""
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
        logger.warning(
            "validate_init_data: подпись не совпала. token_len raw=%s stripped=%s, "
            "поля=%s, computed=%s…, received=%s…",
            len(bot_token), len(token),
            sorted(data.keys()), computed_hash[:12], received_hash[:12],
        )
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
    return {"id": row["id"], "time": row["slot_dt"][-5:]}


def create_app(bot, bot_token: str) -> web.Application:
    app = web.Application()

    def auth_user(init_data: str):
        """Возвращает dict пользователя Telegram из initData или None, если подпись неверна."""
        parsed = validate_init_data(init_data, bot_token)
        if not parsed or not parsed.get("user"):
            # Временный диагностический лог — почему не прошла проверка initData.
            logger.warning(
                "auth_user: проверка не прошла. len(init_data)=%s, начало=%r",
                len(init_data or ""), (init_data or "")[:60],
            )
            return None
        return parsed["user"]

    async def handle_schedule(request: web.Request) -> web.Response:
        try:
            trainer_id = int(request.query.get("trainer_id", ""))
        except ValueError:
            return web.json_response({"error": "bad trainer_id"}, status=400)

        trainer = db.get_trainer(trainer_id)
        if not trainer:
            return web.json_response({"error": "trainer not found"}, status=404)

        days = []
        for day in db.list_free_days(trainer_id):
            slots = db.list_free_slots_for_day(trainer_id, day)
            if slots:
                days.append({"date": day, "slots": [slot_to_dict(s) for s in slots]})

        return web.json_response({
            "trainer_name": trainer["name"],
            "price": trainer["price"],
            "duration_min": trainer["duration_min"],
            "days": days,
        })

    async def handle_my_bookings(request: web.Request) -> web.Response:
        body = await request.json()
        user = auth_user(body.get("initData", ""))
        if not user:
            return web.json_response({"error": "unauthorized"}, status=401)

        rows = db.list_client_bookings(user["id"])
        bookings = [
            {"id": r["id"], "slot_dt": r["slot_dt"], "trainer_name": r["trainer_name"]}
            for r in rows
        ]
        return web.json_response({"bookings": bookings})

    async def handle_book(request: web.Request) -> web.Response:
        body = await request.json()
        user = auth_user(body.get("initData", ""))
        if not user:
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            slot_id = int(body.get("slot_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "bad slot_id"}, status=400)

        full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Без имени"
        username = user.get("username")

        ok = db.book_slot(slot_id, user["id"], full_name, username)
        if not ok:
            return web.json_response({"error": "slot taken"}, status=409)

        slot = db.get_slot(slot_id)
        trainer = db.get_trainer(slot["trainer_id"])

        trainer_contact = ""
        try:
            chat = await bot.get_chat(slot["trainer_id"])
            if chat.username:
                trainer_contact = f"\n💬 Связаться с тренером: @{chat.username}"
        except Exception:
            logger.warning("Не удалось получить контакт тренера %s", slot["trainer_id"])

        try:
            await bot.send_message(
                user["id"],
                f"🎉 Готово! Записал(а) тебя к <b>{esc(trainer['name'])}</b> "
                f"на <b>{fmt_slot(slot['slot_dt'])}</b>.{trainer_contact}",
            )
        except Exception:
            logger.warning("Не удалось отправить подтверждение клиенту %s", user["id"])

        contact = f" (@{esc(username)})" if username else ""
        try:
            await bot.send_message(
                slot["trainer_id"],
                f"🔔 <b>Новая запись!</b>\n{esc(full_name)}{contact} — {fmt_slot(slot['slot_dt'])}",
            )
        except Exception:
            logger.warning("Не удалось уведомить тренера")

        return web.json_response({"ok": True, "slot_dt": slot["slot_dt"], "trainer_name": trainer["name"]})

    async def handle_cancel(request: web.Request) -> web.Response:
        body = await request.json()
        user = auth_user(body.get("initData", ""))
        if not user:
            return web.json_response({"error": "unauthorized"}, status=401)

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
            logger.warning("Не удалось уведомить тренера об отмене")

        return web.json_response({"ok": True})

    app.router.add_get("/api/schedule", handle_schedule)
    app.router.add_post("/api/my", handle_my_bookings)
    app.router.add_post("/api/book", handle_book)
    app.router.add_post("/api/cancel", handle_cancel)
    app.router.add_static("/miniapp/", path=MINIAPP_DIR, show_index=False)

    async def handle_root(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", handle_root)

    return app
