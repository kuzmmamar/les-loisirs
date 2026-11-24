from __future__ import annotations

import asyncio
import email
import imaplib
import os
import re
import tempfile
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from parter.db import decrypt_secret, encrypt_secret, get_conn, get_settings_dict, set_setting
from parter.screenshots import ingest_incoming_item

DEFAULT_MAIL_ADDRESS = "Lesteatres@mail.ru"
DEFAULT_IMAP_HOST = "imap.mail.ru"

EMAIL_TICKET_SUBJECT_HINTS = (
    "билет",
    "заказ",
    "ticket",
    "order",
    "оплат",
    "бланк",
    "театр",
    "большой",
    "bolshoi",
    "вахтангов",
    "vakhtangov",
    "мхт",
    "mxat",
    "наций",
    "рамт",
    "пушкин",
    "сатирикон",
    "озеро",
    "o3ero",
    "radario",
    "ticketland",
    "intickets",
    "яндекс афиш",
    "афиша",
    "kassir",
)

EMAIL_BODY_TICKET_MARKERS = (
    "электронный билет",
    "бланк билета",
    "контрольный купон",
    "заказ оплачен",
    "ваш билет",
    "ваши билеты",
    "билеты во вложении",
    "номер заказа",
    "заказ №",
    "билет №",
    "ряд",
    "место",
    "партер",
    "амфитеатр",
    "бельэтаж",
    "e-ticket",
)

MARKETING_SPAM_MARKERS = (
    "отписаться от рассылки",
    "дайджест недели",
    "подборка событий",
    "рекламная рассылка",
)


def _decode_mime_str(raw: str | None) -> str:
    if not raw:
        return ""
    out: list[str] = []
    for chunk, enc in decode_header(raw):
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(str(chunk))
    return "".join(out).strip()


def _extract_email_body_and_attachments(msg: Message) -> tuple[str, list[tuple[str, bytes]]]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[tuple[str, bytes]] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = (part.get_content_type() or "").lower()
        disp = (part.get("Content-Disposition") or "").lower()
        fname = _decode_mime_str(part.get_filename())
        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        ext = Path(fname).suffix.lower() if fname else ""
        is_ticket_attachment = (
            ext in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
            or ctype == "application/pdf"
            or ("attachment" in disp and ctype.startswith("image/"))
        )

        if is_ticket_attachment:
            if not ext:
                ext = ".pdf" if ctype == "application/pdf" else ".jpg"
            safe_name = fname or f"attachment{ext}"
            # Игнорируем крошечные иконки соцсетей в письмах (< 6 КБ для картинок)
            if ext != ".pdf" and len(payload) < 6000:
                continue
            attachments.append((safe_name, payload))
            continue

        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")

        if ctype == "text/plain":
            plain_parts.append(text)
        elif ctype == "text/html":
            soup = BeautifulSoup(text, "html.parser")
            for bad in soup(["script", "style"]):
                bad.decompose()
            html_parts.append(soup.get_text("\n", strip=True))

    body = "\n".join(plain_parts).strip()
    if len(body) < 40 and html_parts:
        body = "\n".join(html_parts).strip()
    elif html_parts:
        body = body + "\n" + "\n".join(html_parts)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body[:8000], attachments


def _looks_like_ticket_email(subject: str, sender: str, body: str, attachments: list[tuple[str, bytes]]) -> bool:
    has_pdf = any(name.lower().endswith(".pdf") for name, _ in attachments)
    combined_low = f"{subject}\n{sender}\n{body}".lower()

    subj_sender_hit = any(h in f"{subject} {sender}".lower() for h in EMAIL_TICKET_SUBJECT_HINTS)
    body_hits = sum(1 for m in EMAIL_BODY_TICKET_MARKERS if m in combined_low)
    has_date = bool(
        re.search(r"\b\d{1,2}\s+(январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)", combined_low)
        or re.search(r"\b\d{2}[./]\d{2}[./](?:20\d{2}|\d{2})\b", combined_low)
    )

    if has_pdf and (subj_sender_hit or body_hits >= 1):
        return True

    # Отсекаем рекламные рассылки без вложений PDF, если нет явных признаков оплаченного заказа и мест
    if any(sp in combined_low for sp in MARKETING_SPAM_MARKERS) and "заказ" not in combined_low and "оплачен" not in combined_low:
        return False

    return subj_sender_hit and body_hits >= 2 and has_date


def get_mail_credentials() -> tuple[str, str, str]:
    settings = get_settings_dict()
    addr = (os.environ.get("TICKET_MAIL_ADDRESS") or settings.get("mail_address") or DEFAULT_MAIL_ADDRESS).strip()
    host = (os.environ.get("TICKET_MAIL_IMAP_HOST") or settings.get("mail_imap_host") or DEFAULT_IMAP_HOST).strip()
    env_pwd = os.environ.get("TICKET_MAIL_PASSWORD", "").strip()
    if env_pwd:
        return addr, host, env_pwd
    enc = settings.get("mail_password_encrypted", "")
    pwd = decrypt_secret(enc) if enc else ""
    return addr, host, pwd


def _test_imap_login_sync(host: str, address: str, password: str) -> int:
    clean_pwd = password.replace(" ", "").strip()
    with imaplib.IMAP4_SSL(host, 993, timeout=14) as imap:
        imap.login(address, clean_pwd)
        status, count_data = imap.select("INBOX", readonly=True)
        if status != "OK":
            raise ValueError("Не удалось открыть папку «Входящие» на сервере почты")
        return int(count_data[0].decode() if count_data and count_data[0] else 0)


async def configure_mail_inbox(
    password: str,
    address: str = DEFAULT_MAIL_ADDRESS,
    imap_host: str = DEFAULT_IMAP_HOST,
) -> dict[str, Any]:
    addr = (address or DEFAULT_MAIL_ADDRESS).strip()
    host = (imap_host or DEFAULT_IMAP_HOST).strip()
    clean_pwd = password.replace(" ", "").strip()
    if len(clean_pwd) < 6:
        raise ValueError("Введите пароль для внешнего приложения Mail.ru (обычно 16 символов)")

    try:
        total_msgs = await asyncio.to_thread(_test_imap_login_sync, host, addr, clean_pwd)
    except imaplib.IMAP4.error as exc:
        raise ValueError(
            "Mail.ru отклонил вход по IMAP. В Mail.ru нужен специальный «Пароль для внешнего приложения» "
            "(Аккаунт Mail.ru → Безопасность → Пароли для внешних приложений)."
        ) from exc
    except Exception as exc:
        raise ValueError(f"Ошибка подключения к {host}: {exc}") from exc

    set_setting("mail_address", addr)
    set_setting("mail_imap_host", host)
    set_setting("mail_password_encrypted", encrypt_secret(clean_pwd))
    now_hm = datetime.now().strftime("%d.%m %H:%M")
    set_setting("mail_status", f"Подключена · писем во Входящих: {total_msgs}")
    set_setting("mail_last_check_at", now_hm)

    sync_res = await poll_ticket_email_inbox()
    return {
        "address": addr,
        "status": get_settings_dict().get("mail_status", "Подключена"),
        "imported_now": sync_res.get("imported", 0),
    }


def _fetch_unprocessed_candidates_sync(host: str, address: str, password: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        seen_uids = {
            r["uid_key"]
            for r in conn.execute("SELECT uid_key FROM processed_emails").fetchall()
        }

    since_dt = (datetime.now() - timedelta(days=75)).strftime("%d-%b-%Y")
    candidates: list[dict[str, Any]] = []

    with imaplib.IMAP4_SSL(host, 993, timeout=20) as imap:
        imap.login(address, password)
        status, _ = imap.select("INBOX", readonly=True)
        if status != "OK":
            return []

        typ, data = imap.uid("search", None, f'(SINCE "{since_dt}")')
        if typ != "OK" or not data or not data[0]:
            typ, data = imap.uid("search", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return []

        all_uids = [u.decode() for u in data[0].split() if u]
        # Проверяем последние 80 писем, которых ещё нет в processed_emails
        recent_uids = [u for u in all_uids[-80:] if f"{address}:{u}" not in seen_uids]

        for uid in recent_uids:
            uid_key = f"{address}:{uid}"
            ftyp, fdata = imap.uid("fetch", uid, "(RFC822)")
            if ftyp != "OK" or not fdata:
                continue
            raw_bytes = b""
            for item in fdata:
                if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                    raw_bytes = bytes(item[1])
                    break
            if not raw_bytes:
                continue

            msg = email.message_from_bytes(raw_bytes)
            subject = _decode_mime_str(msg.get("Subject"))
            sender = _decode_mime_str(msg.get("From"))
            msg_id = (msg.get("Message-ID") or "").strip()
            date_hdr = (msg.get("Date") or "").strip()
            body, attachments = _extract_email_body_and_attachments(msg)

            candidates.append(
                {
                    "uid_key": uid_key,
                    "message_id": msg_id,
                    "subject": subject,
                    "sender": sender,
                    "received_at": date_hdr,
                    "body": body,
                    "attachments": attachments,
                }
            )
    return candidates


async def poll_ticket_email_inbox() -> dict[str, Any]:
    addr, host, pwd = get_mail_credentials()
    if not pwd:
        return {"connected": False, "checked": 0, "imported": 0}

    try:
        candidates = await asyncio.to_thread(_fetch_unprocessed_candidates_sync, host, addr, pwd)
    except Exception as exc:
        set_setting("mail_status", f"Ошибка проверки: проверьте пароль Mail.ru ({type(exc).__name__})")
        return {"connected": False, "checked": 0, "imported": 0, "error": str(exc)}

    imported_count = 0
    now_iso = datetime.now().isoformat(timespec="seconds")

    for cand in candidates:
        is_ticket = _looks_like_ticket_email(
            cand["subject"],
            cand["sender"],
            cand["body"],
            cand["attachments"],
        )

        ticket_id = 0
        kind_str = "ignored"

        if is_ticket:
            kind_str = "ticket"
            combined_text = f"Тема письма: {cand['subject']}\nОт: {cand['sender']}\n{cand['body']}"
            pdf_atts = [(n, b) for n, b in cand["attachments"] if n.lower().endswith(".pdf")]
            target_atts = pdf_atts if pdf_atts else cand["attachments"][:1]

            if target_atts:
                for fname, raw_blob in target_atts[:3]:
                    ext = Path(fname).suffix.lower() or ".pdf"
                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                        tmp.write(raw_blob)
                        tmp_path = Path(tmp.name)
                    try:
                        res = await ingest_incoming_item(
                            src_path=tmp_path,
                            text_message=combined_text,
                            source="email_lesteatres",
                            sender_name=addr,
                            who_goes="А + +1",
                            force_kind="ticket",
                        )
                        ticket_id = int(res.get("id") or 0)
                        imported_count += 1
                    finally:
                        tmp_path.unlink(missing_ok=True)
            else:
                res = await ingest_incoming_item(
                    src_path=None,
                    text_message=combined_text,
                    source="email_lesteatres",
                    sender_name=addr,
                    who_goes="А + +1",
                    force_kind="ticket",
                )
                ticket_id = int(res.get("id") or 0)
                imported_count += 1

        with get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO processed_emails (
                    uid_key, message_id, subject, sender, received_at, kind, ticket_id, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cand["uid_key"],
                    cand["message_id"],
                    cand["subject"][:200],
                    cand["sender"][:200],
                    cand["received_at"][:80],
                    kind_str,
                    ticket_id,
                    now_iso,
                ),
            )

    now_hm = datetime.now().strftime("%d.%m %H:%M")
    prev_total = int(get_settings_dict().get("mail_imported_count", "0") or 0)
    new_total = prev_total + imported_count
    set_setting("mail_imported_count", str(new_total))
    set_setting("mail_last_check_at", now_hm)
    set_setting("mail_status", f"Активна · проверено {now_hm} · билетов с почты: {new_total}")

    return {"connected": True, "checked": len(candidates), "imported": imported_count}


async def mail_polling_loop() -> None:
    """Регулярное фоновое чтение почты Lesteatres@mail.ru каждые 3 минуты."""
    await asyncio.sleep(3)
    while True:
        try:
            _, _, pwd = get_mail_credentials()
            if pwd:
                await poll_ticket_email_inbox()
        except Exception:
            pass
        await asyncio.sleep(180)
