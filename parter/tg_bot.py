from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from parter.db import decrypt_secret, encrypt_secret, get_settings_dict, set_setting
from parter.planner import format_human_date
from parter.screenshots import ingest_incoming_item


def get_active_bot_token() -> str:
    env_tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if env_tok:
        return env_tok
    settings = get_settings_dict()
    enc = settings.get("tg_bot_token_encrypted", "")
    return decrypt_secret(enc) if enc else ""


async def configure_telegram_bot(raw_token: str) -> dict[str, Any]:
    token = raw_token.strip()
    if not token or ":" not in token:
        raise ValueError("Токен должен иметь формат 123456789:AA... от @BotFather")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
            if not data.get("ok"):
                raise ValueError("Telegram отклонил токен — проверьте скопированную строку от @BotFather")
            bot_user = data["result"].get("username", "")
    except httpx.HTTPError as exc:
        raise ValueError(f"Нет связи с api.telegram.org ({exc})") from exc

    set_setting("tg_bot_token_encrypted", encrypt_secret(token))
    set_setting("tg_bot_username", f"@{bot_user}" if bot_user else "Подключён")
    set_setting("tg_bot_status", f"Активен (@{bot_user})")
    return {"username": f"@{bot_user}", "status": f"Активен (@{bot_user})"}


async def _download_tg_file(client: httpx.AsyncClient, token: str, file_id: str, fallback_ext: str) -> Path:
    info_resp = await client.get(f"https://api.telegram.org/bot{token}/getFile", params={"file_id": file_id})
    file_path = info_resp.json()["result"]["file_path"]
    ext = Path(file_path).suffix.lower() or fallback_ext
    dl_resp = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(dl_resp.content)
        return Path(tmp.name)


async def _handle_tg_message(client: httpx.AsyncClient, token: str, msg: dict[str, Any]) -> None:
    chat_id = msg.get("chat", {}).get("id")
    if not chat_id:
        return

    sender = msg.get("from", {})
    sender_name = sender.get("first_name") or sender.get("username") or "Участник"
    text = (msg.get("text") or msg.get("caption") or "").strip()
    set_setting("tg_primary_chat_id", str(chat_id))

    if text.startswith("/start"):
        welcome = (
            "Привет! Это приёмник билетов и афиш приложения «Les Loisirs».\n\n"
            "Сюда можно пересылать всё подряд:\n"
            "1️⃣ Уже купленные билеты (PDF, скриншоты с QR-кодом, письма касс) — я сохраню их в «Билеты» и закрою этот вечер в недельном календаре.\n"
            "2️⃣ Скриншоты сторис, афиш и посты из каналов — я распознаю спектакль или выставку и поставлю в «Хочу»."
        )
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": welcome},
        )
        return

    tmp_file: Path | None = None
    force_kind = "auto"
    if "#билет" in text.lower():
        force_kind = "ticket"
    elif "#хочу" in text.lower():
        force_kind = "wishlist"

    try:
        if msg.get("document"):
            doc = msg["document"]
            orig_ext = Path(doc.get("file_name") or "ticket.pdf").suffix.lower() or ".pdf"
            tmp_file = await _download_tg_file(client, token, doc["file_id"], orig_ext)
        elif msg.get("photo"):
            best_photo = msg["photo"][-1]
            tmp_file = await _download_tg_file(client, token, best_photo["file_id"], ".jpg")
        elif not text:
            return

        res = await ingest_incoming_item(
            src_path=tmp_file,
            text_message=text,
            source="telegram_bot",
            sender_name=sender_name,
            force_kind=force_kind,
        )

        if res["kind"] == "ticket":
            dt_info = format_human_date(res["event_date"])
            reply = (
                f"🎟 Купленный билет сохранён (прислал: {sender_name})\n\n"
                f"• {res['title']}\n"
                f"• {dt_info['weekday_full']}, {dt_info['full_date']} в {res['event_time']}\n"
                f"• {res['venue']}\n"
                f"• {res['seats_info']}\n\n"
                f"Вечер отмечен занятым в недельном календаре."
            )
        else:
            reply = (
                f"⚡ Добавлено в «Охоту» (прислал: {sender_name})\n\n"
                f"• {res['title']}\n"
                f"• {res['venue']}\n"
                f"Слежу за расписанием и местами (1-й / 5-й ряд / у прохода)."
            )

        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": reply},
        )
    finally:
        if tmp_file:
            tmp_file.unlink(missing_ok=True)


async def telegram_polling_loop() -> None:
    """Фоновый Long Polling Telegram-бота — включается сам, как только добавлен токен."""
    while True:
        token = get_active_bot_token()
        if not token:
            await asyncio.sleep(5)
            continue

        settings = get_settings_dict()
        offset = int(settings.get("tg_last_update_id", "0") or 0)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{token}/getUpdates",
                    params={"offset": offset + 1, "timeout": 20},
                )
                payload = resp.json()
                if payload.get("ok"):
                    for upd in payload.get("result", []):
                        upd_id = upd["update_id"]
                        set_setting("tg_last_update_id", str(upd_id))
                        msg = upd.get("message") or upd.get("channel_post")
                        if msg:
                            await _handle_tg_message(client, token, msg)
        except Exception:
            await asyncio.sleep(5)
