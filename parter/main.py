from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from parter.checkout import (
    cancel_order,
    confirm_order_payment,
    create_checkout_order,
    delete_wallet_ticket,
    list_orders,
    list_wallet_tickets,
)
from parter.db import (
    DATA_DIR,
    SCREENSHOTS_DIR,
    TICKETS_DIR,
    add_away_period,
    add_manual_liked_play,
    add_person_rule,
    add_tracked_creator,
    delete_away_period,
    delete_manual_liked_play,
    delete_person_rule,
    delete_tracked_creator,
    encrypt_secret,
    ensure_dirs,
    get_conn,
    get_person_taste_summary,
    get_settings_dict,
    init_db,
    list_away_periods,
    list_companions,
    list_post_show_state,
    set_person_decision,
    set_setting,
    submit_post_show_review,
    toggle_production_like,
)
from parter.mail_inbox import (
    configure_mail_inbox,
    get_mail_credentials,
    mail_polling_loop,
    poll_ticket_email_inbox,
)
from parter.planner import build_liked_overview, build_weekly_proposals, format_human_date, list_repertoire
from parter.scraper import is_safe_official_url, scan_official_theaters
from parter.screenshots import ingest_incoming_item, list_screenshots, poll_photos_inbox
from parter.tg_bot import configure_telegram_bot, telegram_polling_loop

STATIC_DIR = Path(__file__).resolve().parent / "static"
ACCESS_KEY_PATH = DATA_DIR / "access.key"
_LOCK_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Les Loisirs</title></head>
<body style="margin:0;background:#0c0b0e;color:#f4efe6;font:16px/1.4 -apple-system,sans-serif">
<main style="max-width:430px;margin:0 auto;padding:28px 20px">
<h1 style="font-size:22px;font-weight:600">Les Loisirs</h1>
<p>Откройте полную ссылку приложения. Эта страница без неё ничего не показывает.</p>
</main></body></html>"""


def _access_key() -> str:
    ensure_dirs()
    if not ACCESS_KEY_PATH.exists():
        ACCESS_KEY_PATH.write_text(secrets.token_urlsafe(18), encoding="utf-8")
        ACCESS_KEY_PATH.chmod(0o600)
    return ACCESS_KEY_PATH.read_text(encoding="utf-8").strip()


def _same(left: str, right: str) -> bool:
    if not left or len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def _direct_lan(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback


async def background_loops() -> None:
    try:
        await scan_official_theaters()
    except Exception:
        pass

    last_daily_date = datetime.now().date()
    while True:
        try:
            await poll_photos_inbox()
            now = datetime.now()
            settings = get_settings_dict()
            target_hm = settings.get("daily_scan_time", "09:30")
            if now.date() != last_daily_date and now.strftime("%H:%M") >= target_hm:
                await scan_official_theaters()
                last_daily_date = now.date()
        except Exception:
            pass
        await asyncio.sleep(45)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_dirs()
    _access_key()
    init_db()
    scan_task = asyncio.create_task(background_loops())
    tg_task = asyncio.create_task(telegram_polling_loop())
    mail_task = asyncio.create_task(mail_polling_loop())
    try:
        yield
    finally:
        scan_task.cancel()
        tg_task.cancel()
        mail_task.cancel()


ensure_dirs()
app = FastAPI(title="Les Loisirs — Мобильный театральный консьерж", lifespan=lifespan)


@app.middleware("http")
async def disable_html_and_static_cache(request: Request, call_next: Any) -> Any:
    response = await call_next(request)
    p = request.url.path
    if p in ("/", "/sw.js", "/manifest.webmanifest") or p.startswith("/static/") or p.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.middleware("http")
async def require_remote_access(request: Request, call_next: Any) -> Any:
    client = request.client.host if request.client else ""
    if _direct_lan(client):
        return await call_next(request)
    key = _access_key()
    provided = request.query_params.get("k", "")
    ok = (
        _same(provided, key)
        or _same(request.cookies.get("parter_access", ""), key)
        or _same(request.headers.get("x-parter-key", ""), key)
    )
    if not ok:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Нужна ссылка доступа"}, status_code=401)
        return HTMLResponse(_LOCK_HTML, status_code=401)
    if request.method == "GET" and _same(provided, key) and request.url.path in ("/", ""):
        response = RedirectResponse("/", status_code=302)
    else:
        response = await call_next(request)
    if _same(provided, key):
        response.set_cookie(
            "parter_access",
            key,
            max_age=60 * 60 * 24 * 400,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/media/screenshots", StaticFiles(directory=str(SCREENSHOTS_DIR)), name="screenshots")
app.mount("/media/tickets", StaticFiles(directory=str(TICKETS_DIR)), name="tickets")


def _app_build() -> str:
    files = (
        STATIC_DIR / "index.html",
        STATIC_DIR / "app.js",
        STATIC_DIR / "styles.css",
        STATIC_DIR / "sw.js",
    )
    stamp = 0
    for path in files:
        if path.exists():
            stamp = max(stamp, int(path.stat().st_mtime))
    return str(stamp)


@app.get("/api/app-version")
async def api_app_version() -> dict[str, str]:
    return {"build": _app_build()}


@app.get("/")
async def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('data-build=""', f'data-build="{_app_build()}"', 1)
    return HTMLResponse(html)


@app.get("/manifest.webmanifest")
async def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


def _norm_person(raw: str | None) -> str:
    val = (raw or "А").strip()
    if val in ("+1", "А+1", "К"):
        return "+1"
    return "А"


@app.get("/api/state")
async def api_state(person: str = "А") -> dict[str, Any]:
    active_person = _norm_person(person)
    liked_overview = build_liked_overview(active_person)
    post_show = list_post_show_state(active_person)
    for item in post_show["pending"]:
        if item.get("event_date"):
            item.update(format_human_date(item["event_date"]))
    proposals = build_weekly_proposals(active_person)
    repertoire = list_repertoire(active_person)
    proposals["inbox_count"] = sum(1 for r in repertoire if r["status"] == "inbox")
    proposals["swiped_want_count"] = sum(1 for r in repertoire if r["status"] in ("want", "sniper"))
    companions = list_companions()
    orders = list_orders()
    wallet_raw = list_wallet_tickets()
    wallet_tickets = [{**w, **format_human_date(w["event_date"])} for w in wallet_raw]
    away_raw = list_away_periods()
    away_periods = [
        {
            **a,
            "start_human": format_human_date(a["start_date"])["label"],
            "end_human": format_human_date(a["end_date"])["label"],
        }
        for a in away_raw
    ]
    screenshots = list_screenshots()
    settings = get_settings_dict()
    mail_addr, mail_host, mail_pwd = get_mail_credentials()
    taste_profiles = {
        "А": get_person_taste_summary("А"),
        "+1": get_person_taste_summary("+1"),
    }

    with get_conn() as conn:
        sources = [dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY id ASC").fetchall()]

    return {
        "active_person": active_person,
        "taste_profiles": taste_profiles,
        "liked": liked_overview,
        "post_show": post_show,
        "proposals": proposals,
        "repertoire": repertoire,
        "companions": companions,
        "orders": orders,
        "wallet_tickets": wallet_tickets,
        "away_periods": away_periods,
        "screenshots": screenshots,
        "sources": sources,
        "settings": {
            "max_price_per_ticket": int(settings.get("max_price_per_ticket", "60000") or 60000),
            "seat_rule": settings.get("seat_rule", ""),
            "autonomy_mode": settings.get("autonomy_mode", "hybrid_sniper"),
            "ticket_sources_policy": settings.get("ticket_sources_policy", "include_mos_id"),
            "payment_method": settings.get("payment_method", "card_3ds"),
            "card_masked": settings.get("card_masked", ""),
            "mos_id_connected": bool(settings.get("mos_id_encrypted", "")),
            "mos_id_masked": settings.get("mos_id_masked", ""),
            "daily_scan_time": settings.get("daily_scan_time", "09:30"),
            "last_scan_at": settings.get("last_scan_at", ""),
            "last_scan_summary": settings.get("last_scan_summary", ""),
            "tg_bot_username": settings.get("tg_bot_username", ""),
            "tg_bot_status": settings.get("tg_bot_status", "Ожидает токен от @BotFather"),
            "mail_address": mail_addr,
            "mail_imap_host": mail_host,
            "mail_connected": bool(mail_pwd),
            "mail_status": settings.get("mail_status", "Ожидает пароль приложения Mail.ru"),
            "mail_last_check_at": settings.get("mail_last_check_at", ""),
        },
    }


@app.post("/api/scan")
async def api_scan_now() -> dict[str, Any]:
    inbox_items = await poll_photos_inbox()
    mail_res = await poll_ticket_email_inbox()
    scan_res = await scan_official_theaters()
    return {"scan": scan_res, "inbox_processed": len(inbox_items), "mail": mail_res}


class ProductionUpdatePayload(BaseModel):
    status: str | None = None
    person_name: str = "А"
    party_ids: list[int] | None = None


@app.post("/api/productions/{slug}")
async def api_update_production(slug: str, payload: ProductionUpdatePayload) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    person_name = _norm_person(payload.person_name)
    with get_conn() as conn:
        row = conn.execute("SELECT slug FROM productions WHERE slug = ?", (slug,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Спектакль не найден")
        if payload.party_ids is not None and len(payload.party_ids) >= 1:
            conn.execute(
                "UPDATE productions SET default_party = ?, updated_at = ? WHERE slug = ?",
                (json.dumps(payload.party_ids[:3]), now, slug),
            )
    if payload.status is not None:
        if payload.status not in {"inbox", "want", "sniper", "seen", "skip"}:
            raise HTTPException(status_code=400, detail="Некорректный статус")
        set_person_decision(person_name, slug, payload.status)
    return {"ok": True, "taste": get_person_taste_summary(person_name)}


class CheckoutPayload(BaseModel):
    event_id: int
    ticket_count: int = 2
    party_ids: list[int]
    mode: str = "manual_approve"


@app.post("/api/orders")
async def api_create_order(payload: CheckoutPayload) -> dict[str, Any]:
    try:
        order = create_checkout_order(
            payload.event_id,
            payload.party_ids,
            ticket_count=payload.ticket_count,
            mode=payload.mode,
        )
        return {"ok": True, "order": order}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ConfirmPaymentPayload(BaseModel):
    three_ds_code: str = ""


@app.post("/api/orders/{order_id}/confirm")
async def api_confirm_order(order_id: int, payload: ConfirmPaymentPayload) -> dict[str, Any]:
    try:
        order = confirm_order_payment(order_id, three_ds_code=payload.three_ds_code)
        return {"ok": True, "order": order}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/orders/{order_id}/cancel")
async def api_cancel_order(order_id: int) -> dict[str, Any]:
    cancel_order(order_id)
    return {"ok": True}


@app.post("/api/incoming/upload")
async def api_upload_incoming(
    file: UploadFile = File(...),
    caption: str = Form(""),
    force_kind: str = Form("auto"),
    who_goes: str = Form("А + +1"),
    source: str = Form("iphone_app"),
) -> dict[str, Any]:
    suffix = Path(file.filename or "item.jpg").suffix.lower() or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        item = await ingest_incoming_item(
            src_path=tmp_path,
            text_message=caption,
            source=source,
            who_goes=who_goes,
            force_kind=force_kind,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"ok": True, "item": item}


@app.post("/api/screenshots/upload")
async def api_upload_screenshot(
    file: UploadFile = File(...),
    caption: str = Form(""),
    source: str = Form("iphone_app"),
) -> dict[str, Any]:
    return await api_upload_incoming(file=file, caption=caption, force_kind="auto", source=source)


@app.post("/api/inbox/shortcut")
async def api_ios_shortcut_inbox(
    file: UploadFile = File(...),
    note: str = Form(""),
) -> dict[str, Any]:
    return await api_upload_incoming(file=file, caption=note, force_kind="auto", source="ios_photos_album")


class ManualTicketPayload(BaseModel):
    title: str
    venue_name: str = ""
    event_date: str
    event_time: str = "19:00"
    seats_info: str = ""
    who_goes: str = "А + +1"


@app.post("/api/wallet-tickets")
async def api_add_manual_ticket(payload: ManualTicketPayload) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO wallet_tickets (
                title, venue_name, event_date, event_time, seats_info,
                who_goes, sender_name, filename, file_kind, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'Вручную', '', 'manual', 'manual', ?)
            """,
            (
                payload.title.strip(),
                payload.venue_name.strip(),
                payload.event_date,
                payload.event_time,
                payload.seats_info.strip(),
                payload.who_goes.strip(),
                now,
            ),
        )
        conn.execute(
            "UPDATE productions SET status = 'seen', sniper_enabled = 0, updated_at = ? WHERE lower(title) = lower(?)",
            (now, payload.title.strip()),
        )
    return {"ok": True}


@app.delete("/api/wallet-tickets/{ticket_id}")
async def api_delete_wallet_ticket(ticket_id: int) -> dict[str, Any]:
    delete_wallet_ticket(ticket_id)
    return {"ok": True}


class MailConfigPayload(BaseModel):
    password: str
    address: str = "Lesteatres@mail.ru"
    imap_host: str = "imap.mail.ru"


@app.post("/api/settings/mail")
async def api_save_mail_inbox(payload: MailConfigPayload) -> dict[str, Any]:
    try:
        res = await configure_mail_inbox(
            password=payload.password,
            address=payload.address,
            imap_host=payload.imap_host,
        )
        return {"ok": True, **res}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/mail/check")
async def api_check_mail_now() -> dict[str, Any]:
    res = await poll_ticket_email_inbox()
    return {"ok": True, **res}


class TelegramConfigPayload(BaseModel):
    bot_token: str


@app.post("/api/settings/telegram")
async def api_save_telegram_bot(payload: TelegramConfigPayload) -> dict[str, Any]:
    try:
        res = await configure_telegram_bot(payload.bot_token)
        return {"ok": True, **res}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CompanionPayload(BaseModel):
    id: int | None = None
    name: str
    full_name: str = ""
    birth_date: str = ""
    doc_type: str = "Паспорт РФ"
    doc_number: str = ""
    phone: str = ""
    email: str = ""


@app.post("/api/companions")
async def api_save_companion(payload: CompanionPayload) -> dict[str, Any]:
    doc_enc = encrypt_secret(payload.doc_number.strip()) if payload.doc_number.strip() else ""
    digits = "".join(ch for ch in payload.doc_number if ch.isdigit())
    doc_masked = f"{payload.doc_type} •• {digits[-4:]}" if len(digits) >= 4 else ("Сохранено" if doc_enc else "Не заполнен")

    with get_conn() as conn:
        if payload.id:
            if doc_enc:
                conn.execute(
                    """
                    UPDATE companions
                    SET name = ?, full_name = ?, birth_date = ?, doc_type = ?, doc_encrypted = ?, doc_masked = ?, phone = ?, email = ?
                    WHERE id = ?
                    """,
                    (payload.name.strip(), payload.full_name.strip(), payload.birth_date.strip(), payload.doc_type, doc_enc, doc_masked, payload.phone.strip(), payload.email.strip(), payload.id),
                )
            else:
                conn.execute(
                    """
                    UPDATE companions
                    SET name = ?, full_name = ?, birth_date = ?, doc_type = ?, phone = ?, email = ?
                    WHERE id = ?
                    """,
                    (payload.name.strip(), payload.full_name.strip(), payload.birth_date.strip(), payload.doc_type, payload.phone.strip(), payload.email.strip(), payload.id),
                )
        else:
            conn.execute(
                """
                INSERT INTO companions (name, role, full_name, birth_date, doc_type, doc_encrypted, doc_masked, phone, email, is_self)
                VALUES (?, 'companion', ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (payload.name.strip(), payload.full_name.strip(), payload.birth_date.strip(), payload.doc_type, doc_enc, doc_masked, payload.phone.strip(), payload.email.strip()),
            )
    return {"ok": True, "companions": list_companions()}


class CardVaultPayload(BaseModel):
    payment_method: str = "card_3ds"
    card_number: str = ""
    card_holder: str = ""
    card_expiry: str = ""
    card_cvc: str = ""
    max_price_per_ticket: int = 60000


@app.post("/api/settings/card")
async def api_save_card(payload: CardVaultPayload) -> dict[str, Any]:
    if payload.payment_method in ("sbp", "card_3ds"):
        set_setting("payment_method", payload.payment_method)
    digits = "".join(ch for ch in payload.card_number if ch.isdigit())
    cvc_digits = "".join(ch for ch in payload.card_cvc if ch.isdigit())
    if digits:
        if len(digits) < 12:
            raise HTTPException(status_code=400, detail="Проверьте номер карты")
        secret_blob = json.dumps(
            {
                "pan": digits,
                "holder": payload.card_holder.strip(),
                "exp": payload.card_expiry.strip(),
                "cvc": cvc_digits[:4],
            }
        )
        set_setting("card_encrypted", encrypt_secret(secret_blob))
        set_setting("card_masked", f"Карта •••• {digits[-4:]}")
    if payload.max_price_per_ticket >= 1000:
        set_setting("max_price_per_ticket", str(payload.max_price_per_ticket))
    return {"ok": True}


class MosIdVaultPayload(BaseModel):
    login: str
    password: str = ""
    ticket_sources_policy: str = "include_mos_id"


@app.post("/api/settings/mos-id")
async def api_save_mos_id(payload: MosIdVaultPayload) -> dict[str, Any]:
    login_clean = payload.login.strip()
    if payload.ticket_sources_policy in ("include_mos_id", "direct_plus_partners", "direct_sites_only"):
        set_setting("ticket_sources_policy", payload.ticket_sources_policy)
    if login_clean:
        pwd_clean = payload.password.strip()
        if not pwd_clean and not get_settings_dict().get("mos_id_encrypted"):
            raise HTTPException(status_code=400, detail="Введите пароль от mos.ru (хранится зашифрованно)")
        digits = "".join(ch for ch in login_clean if ch.isdigit())
        if len(digits) >= 10:
            masked = f"Mos ID ••• {digits[-4:]}"
        elif "@" in login_clean:
            user_p, dom_p = login_clean.split("@", 1)
            masked = f"Mos ID {user_p[:2]}•••@{dom_p}"
        else:
            masked = f"Mos ID •••{login_clean[-3:]}"
        if pwd_clean:
            blob = json.dumps({"login": login_clean, "password": pwd_clean})
            set_setting("mos_id_encrypted", encrypt_secret(blob))
        set_setting("mos_id_masked", masked)
    return {"ok": True}


class SourcePayload(BaseModel):
    kind: str
    handle_or_url: str
    title: str


@app.post("/api/sources")
async def api_add_source(payload: SourcePayload) -> dict[str, Any]:
    raw = payload.handle_or_url.strip()
    if raw.startswith("http") and not is_safe_official_url(raw):
        raise HTTPException(
            status_code=400,
            detail="Этот домен в списке перекупщиков-двойников. Укажите официальный сайт площадки.",
        )
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sources (kind, handle_or_url, title) VALUES (?, ?, ?)",
            (payload.kind, raw, payload.title.strip() or raw),
        )
    return {"ok": True}


@app.delete("/api/sources/{source_id}")
async def api_delete_source(source_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return {"ok": True}


class AwayPeriodPayload(BaseModel):
    start_date: str
    end_date: str = ""
    person_name: str = "Я"
    is_primary: bool = True
    note: str = "Нет в Москве"


@app.post("/api/away-periods")
async def api_add_away_period(payload: AwayPeriodPayload) -> dict[str, Any]:
    item = add_away_period(
        start_date=payload.start_date,
        end_date=payload.end_date or payload.start_date,
        person_name=payload.person_name,
        is_primary=payload.is_primary,
        note=payload.note,
    )
    return {"ok": True, "item": item}


@app.delete("/api/away-periods/{period_id}")
async def api_delete_away_period(period_id: int) -> dict[str, Any]:
    delete_away_period(period_id)
    return {"ok": True}


class PersonRulePayload(BaseModel):
    person_name: str = "А"
    rule_kind: str
    rule_value: str
    label: str


@app.post("/api/person-rules")
async def api_add_person_rule(payload: PersonRulePayload) -> dict[str, Any]:
    item = add_person_rule(
        person_name=_norm_person(payload.person_name),
        rule_kind=payload.rule_kind,
        rule_value=payload.rule_value,
        label=payload.label,
    )
    return {"ok": True, "item": item}


@app.delete("/api/person-rules/{rule_id}")
async def api_delete_person_rule(rule_id: int) -> dict[str, Any]:
    delete_person_rule(rule_id)
    return {"ok": True}


class ToggleLikePayload(BaseModel):
    slug: str
    liked: bool = True
    person_name: str = "А"


@app.post("/api/liked/toggle")
async def api_toggle_liked(payload: ToggleLikePayload) -> dict[str, Any]:
    toggle_production_like(payload.slug, payload.liked, person_name=_norm_person(payload.person_name))
    return {"ok": True}


class ManualLikedPlayPayload(BaseModel):
    title: str
    stage_director: str = ""
    venue_name: str = ""
    category: str = "theatre"
    note: str = ""
    person_name: str = "А"


@app.post("/api/liked/manual")
async def api_add_manual_liked(payload: ManualLikedPlayPayload) -> dict[str, Any]:
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Укажите название спектакля")
    item = add_manual_liked_play(
        title=payload.title,
        stage_director=payload.stage_director,
        venue_name=payload.venue_name,
        category=payload.category,
        note=payload.note,
        person_name=_norm_person(payload.person_name),
    )
    return {"ok": True, "item": item}


@app.delete("/api/liked/manual/{item_id}")
async def api_delete_manual_liked(item_id: int) -> dict[str, Any]:
    delete_manual_liked_play(item_id)
    return {"ok": True}


class TrackedCreatorPayload(BaseModel):
    person_name: str = "А"
    name: str
    role: str = "Режиссёр"
    source_note: str = "Добавлено вручную"


@app.post("/api/tracked-creators")
async def api_add_tracked_creator(payload: TrackedCreatorPayload) -> dict[str, Any]:
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Укажите имя режиссёра, хореографа или театр")
    item = add_tracked_creator(
        person_name=_norm_person(payload.person_name),
        name=payload.name,
        role=payload.role,
        source_note=payload.source_note,
    )
    return {"ok": True, "item": item}


@app.delete("/api/tracked-creators/{creator_id}")
async def api_delete_tracked_creator(creator_id: int) -> dict[str, Any]:
    delete_tracked_creator(creator_id)
    return {"ok": True}


class PostShowReviewPayload(BaseModel):
    item_key: str
    person_name: str = "А"
    verdict: str  # 'liked' | 'disliked' | 'dismissed'
    title: str
    venue_name: str = ""
    stage_director: str = ""
    production_slug: str = ""
    event_date: str = ""


@app.post("/api/post-show-review")
async def api_submit_post_show_review(payload: PostShowReviewPayload) -> dict[str, Any]:
    try:
        res = submit_post_show_review(
            item_key=payload.item_key,
            person_name=_norm_person(payload.person_name),
            verdict=payload.verdict,
            title=payload.title,
            venue_name=payload.venue_name,
            stage_director=payload.stage_director,
            production_slug=payload.production_slug,
            event_date=payload.event_date,
        )
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc



