from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from parter.db import PHOTOS_INBOX_DIR, SCREENSHOTS_DIR, TICKETS_DIR, ensure_dirs, get_conn
from parter.scraper import parse_russian_dates_and_prices

SWIFT_OCR_OR_PDF_SCRIPT = r"""
import Foundation
import Vision
import AppKit
import PDFKit

let args = CommandLine.arguments
guard args.count > 1 else { exit(0) }
let path = args[1]
let url = URL(fileURLWithPath: path)

if path.lowercased().hasSuffix(".pdf"), let pdf = PDFDocument(url: url) {
    var out: [String] = []
    if let direct = pdf.string, !direct.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        out.append(direct)
    }
    // Если PDF отсканированный — прогоняем первую страницу через Vision OCR
    if out.joined().count < 40, let page = pdf.page(at: 0) {
        let img = page.thumbnail(of: NSSize(width: 1600, height: 2200), for: .mediaBox)
        if let cgImage = img.cgImage(forProposedRect: nil, context: nil, hints: nil) {
            let req = VNRecognizeTextRequest()
            req.recognitionLevel = .accurate
            req.recognitionLanguages = ["ru-RU", "en-US"]
            let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
            try? handler.perform([req])
            let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
            out.append(lines.joined(separator: "\n"))
        }
    }
    print(out.joined(separator: "\n"))
    exit(0)
}

guard let img = NSImage(contentsOf: url),
      let cgImage = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    exit(0)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["ru-RU", "en-US"]
request.usesLanguageCorrection = true
let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try? handler.perform([request])
let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
print(lines.joined(separator: "\n"))
"""

TICKET_KEYWORDS = [
    "электронный билет",
    "бланк билета",
    "контрольный купон",
    "билет №",
    "заказ №",
    "номер заказа",
    "штрихкод",
    "штрих-код",
    "e-ticket",
    "ticketland",
    "radario",
    "intickets",
    "яндекс афиша",
    "партер",
    "амфитеатр",
    "бельэтаж",
]


def extract_local_text(file_path: Path) -> str:
    if not Path("/usr/bin/swift").exists():
        return ""
    try:
        proc = subprocess.run(
            ["/usr/bin/swift", "-e", SWIFT_OCR_OR_PDF_SCRIPT, str(file_path)],
            capture_output=True,
            text=True,
            timeout=18,
            check=False,
        )
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def detect_is_purchased_ticket(text: str, ext: str, force_kind: str = "auto") -> bool:
    if force_kind == "ticket":
        return True
    if force_kind == "wishlist":
        return False
    low = text.lower()
    if ext.lower() == ".pdf":
        return True
    has_row_seat = bool(re.search(r"ряд\s*\d+", low) and re.search(r"мест[оа]\s*\d+", low))
    kw_hits = sum(1 for kw in TICKET_KEYWORDS if kw in low)
    return has_row_seat or kw_hits >= 2


def parse_venue_and_director(raw: str) -> tuple[str, str]:
    low = raw.lower()
    known_venues = [
        ("вахтангов", "Театр им. Евг. Вахтангова"),
        ("мхт", "МХТ им. А. П. Чехова"),
        ("мхат", "МХТ им. А. П. Чехова"),
        ("камергерск", "МХТ им. А. П. Чехова"),
        ("наций", "Театр Наций"),
        ("рамт", "РАМТ"),
        ("пушкин", "Театр им. А. С. Пушкина"),
        ("сатирикон", "Театр «Сатирикон»"),
        ("планета квн", "Театр «Сатирикон» (Планета КВН)"),
        ("фоменко", "Мастерская Петра Фоменко"),
        ("большой театр", "Большой театр"),
        ("bolshoi", "Большой театр"),
        ("озеро", "Театр «Озеро»"),
        ("o3ero", "Театр «Озеро»"),
        ("рождественк", "Театр «Озеро»"),
        ("геликон", "Геликон-опера"),
        ("станиславск", "МАМТ им. Станиславского"),
        ("гэс-2", "Дом культуры «ГЭС-2»"),
        ("третьяков", "Третьяковская галерея"),
        ("гараж", "Музей «Гараж»"),
        ("ммси", "ММОМА"),
    ]
    venue = "Площадка в Москве"
    for token, vname in known_venues:
        if token in low:
            venue = vname
            break

    director = ""
    if "туминас" in low:
        director = "Римас Туминас"
    elif "крымов" in low:
        director = "Дмитрий Крымов"
    elif "бутусов" in low:
        director = "Юрий Бутусов"
    return venue, director


def parse_numeric_or_ru_date_time(raw: str) -> tuple[str, str]:
    # Сначала пробуем «26 декабря 2026 ... 19:00»
    dates_ru, _, _ = parse_russian_dates_and_prices(raw + " 19:00")
    if dates_ru:
        time_m = re.search(r"\b([012]?\d:[0-5]\d)\b", raw)
        return dates_ru[0][0], (time_m.group(1) if time_m else dates_ru[0][1])

    # Пробуем цифровой формат на билетах: 26.12.2026 или 26/12/2026
    num_m = re.search(r"\b(\d{2})[./](\d{2})[./](20\d{2}|\d{2})\b", raw)
    if num_m:
        d, m, y_raw = int(num_m.group(1)), int(num_m.group(2)), int(num_m.group(3))
        y = 2000 + y_raw if y_raw < 100 else y_raw
        try:
            iso = date(y, m, d).isoformat()
            tm_m = re.search(r"\b([012]?\d:[0-5]\d)\b", raw)
            return iso, (tm_m.group(1) if tm_m else "19:00")
        except ValueError:
            pass

    return date.today().isoformat(), "19:00"


def parse_seats_and_order(raw: str) -> tuple[str, str]:
    zone_m = re.search(r"(партер|амфитеатр|бельэтаж|балкон|ложа[^\n,;]{0,15})", raw, re.IGNORECASE)
    row_m = re.search(r"ряд\s*[:№]?\s*(\d+)", raw, re.IGNORECASE)
    seat_m = re.search(r"мест[оа]?\s*[:№]?\s*([\d,\s–-]+)", raw, re.IGNORECASE)
    parts = []
    if zone_m:
        parts.append(zone_m.group(1).capitalize())
    if row_m:
        parts.append(f"ряд {row_m.group(1)}")
    if seat_m:
        s_clean = re.sub(r"\s+", " ", seat_m.group(1)).strip(" ,.-")[:18]
        if s_clean:
            parts.append(f"место {s_clean}")
    seats_info = ", ".join(parts) if parts else "Места указаны в бланке билета"

    ord_m = re.search(r"(?:заказ|билет)\s*[№#:]?\s*([A-ZА-Я0-9-]{4,18})", raw, re.IGNORECASE)
    order_num = ord_m.group(1) if ord_m else ""
    return seats_info, order_num


def pick_best_title(raw: str, fallback: str) -> str:
    # Сначала сверяем с известными спектаклями в базе
    with get_conn() as conn:
        known = conn.execute("SELECT title FROM productions").fetchall()
    low = raw.lower()
    for r in known:
        t = r["title"]
        if len(t) >= 3 and t.lower() in low:
            return t

    quoted = re.findall(r"«([^»]{2,65})»", raw)
    if quoted:
        return quoted[0].strip()

    lines = [
        ln.strip()
        for ln in raw.splitlines()
        if len(ln.strip()) >= 3
        and not re.fullmatch(r"[\d:\s%LTE5GWiFi•./-]+", ln.strip(), re.IGNORECASE)
        and "электронный билет" not in ln.lower()
        and "бланк билета" not in ln.lower()
    ]
    return lines[0][:70] if lines else fallback


async def analyze_incoming_text_or_file(
    file_path: Path | None,
    caption_or_text: str = "",
    force_kind: str = "auto",
) -> dict[str, Any]:
    extracted_text = extract_local_text(file_path) if file_path else ""
    combined = "\n".join(x for x in [caption_or_text.strip(), extracted_text.strip()] if x)
    ext = file_path.suffix.lower() if file_path else ""

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
    model = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini")

    if api_key:
        try:
            client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            user_content: list[dict[str, Any]] = [{"type": "text", "text": f"Текст/OCR:\n{combined}"}]
            if file_path and ext in {".jpg", ".jpeg", ".png", ".webp"}:
                mime = "image/png" if ext == ".png" else "image/jpeg"
                b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
                user_content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

            resp = await client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Определи, что прислал пользователь: 1) 'ticket' — уже купленный билет в театр/музей "
                            "или 2) 'wishlist' — анонс/скриншот спектакля или выставки, куда хотят пойти. "
                            "Верни JSON: kind ('ticket'|'wishlist'), title, director, venue, "
                            "event_date (YYYY-MM-DD), event_time (HH:MM), seats_info, order_number, note."
                        ),
                    },
                    {"role": "user", "content": user_content},
                ],
                timeout=20,
            )
            parsed = json.loads(resp.choices[0].message.content or "{}")
            if parsed.get("title"):
                if force_kind in {"ticket", "wishlist"}:
                    parsed["kind"] = force_kind
                return parsed
        except Exception:
            pass

    is_ticket = detect_is_purchased_ticket(combined, ext, force_kind=force_kind)
    venue, director = parse_venue_and_director(combined)
    ev_date, ev_time = parse_numeric_or_ru_date_time(combined)
    seats_info, order_num = parse_seats_and_order(combined)
    title = pick_best_title(combined, "Купленный билет" if is_ticket else "Событие со скриншота")
    short_lines = [ln.strip() for ln in combined.splitlines() if ln.strip()]
    note = " · ".join(short_lines[:4])[:220] if short_lines else ""

    return {
        "kind": "ticket" if is_ticket else "wishlist",
        "title": title,
        "director": director,
        "venue": venue,
        "event_date": ev_date,
        "event_time": ev_time,
        "seats_info": seats_info,
        "order_number": order_num,
        "note": note,
    }


def slugify_title(title: str) -> str:
    clean = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", "-", title.lower()).strip("-")
    stamp = datetime.now().strftime("%m%d%H%M%S")
    return f"shot-{clean[:24] or 'item'}-{stamp}"


async def ingest_incoming_item(
    src_path: Path | None = None,
    text_message: str = "",
    source: str = "upload",
    sender_name: str = "Я",
    who_goes: str = "А + +1",
    force_kind: str = "auto",
) -> dict[str, Any]:
    """
    Единая точка приёма из Telegram-бота, папки «Фото» и мобильного приложения:
    - если это купленный билет -> сохраняет бланк в data/tickets и в wallet_tickets, блокируя вечер в календаре;
    - если это анонс/хотелка -> сохраняет в screenshots и ставит в «Охоту ⚡».
    """
    ensure_dirs()
    parsed = await analyze_incoming_text_or_file(src_path, caption_or_text=text_message, force_kind=force_kind)
    now = datetime.now().isoformat(timespec="seconds")
    kind = parsed.get("kind", "wishlist")

    if kind == "ticket":
        saved_name = ""
        file_kind = "text"
        if src_path and src_path.exists():
            ext = src_path.suffix.lower() or ".pdf"
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            saved_name = f"ticket-{stamp}{ext}"
            shutil.copy2(src_path, TICKETS_DIR / saved_name)
            file_kind = "pdf" if ext == ".pdf" else "image"

        ev_date = parsed.get("event_date") or date.today().isoformat()
        ev_time = parsed.get("event_time") or "19:00"
        title = parsed.get("title") or "Купленный билет"
        venue = parsed.get("venue") or "Театр / Выставка"
        seats_info = parsed.get("seats_info") or "См. прикреплённый билет"
        order_num = parsed.get("order_number") or ""

        with get_conn() as conn:
            dup = None
            if order_num:
                dup = conn.execute(
                    "SELECT id, filename, seats_info FROM wallet_tickets WHERE order_number = ? AND event_date = ?",
                    (order_num, ev_date),
                ).fetchone()
            if not dup:
                dup = conn.execute(
                    "SELECT id, filename, seats_info FROM wallet_tickets WHERE lower(title) = lower(?) AND event_date = ? AND event_time = ?",
                    (title, ev_date, ev_time),
                ).fetchone()

            if dup:
                ticket_id = dup["id"]
                new_file = saved_name if saved_name else dup["filename"]
                new_file_kind = file_kind if saved_name else "pdf"
                new_seats = (
                    seats_info
                    if seats_info and "бланке" not in seats_info and "прикреплённый" not in seats_info
                    else dup["seats_info"]
                )
                conn.execute(
                    """
                    UPDATE wallet_tickets
                    SET filename = ?, file_kind = ?, seats_info = ?, order_number = COALESCE(NULLIF(?, ''), order_number)
                    WHERE id = ?
                    """,
                    (new_file, new_file_kind, new_seats, order_num, ticket_id),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO wallet_tickets (
                        title, venue_name, event_date, event_time, seats_info,
                        who_goes, sender_name, order_number, filename, file_kind, source, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        venue,
                        ev_date,
                        ev_time,
                        seats_info,
                        who_goes,
                        sender_name,
                        order_num,
                        saved_name,
                        file_kind,
                        source,
                        parsed.get("note", ""),
                        now,
                    ),
                )
                ticket_id = cur.lastrowid

            # Если спектакль был в нашем каталоге — отмечаем как «Уже есть билет / Был», чтобы не предлагать повторно
            matched_prods = conn.execute(
                "SELECT slug FROM productions WHERE lower(title) = lower(?)",
                (title,),
            ).fetchall()
            for mp in matched_prods:
                conn.execute(
                    "UPDATE productions SET status = 'seen', sniper_enabled = 0, updated_at = ? WHERE slug = ?",
                    (now, mp["slug"]),
                )
                for p_name in ("А", "+1"):
                    conn.execute(
                        """
                        INSERT INTO person_decisions (person_name, production_slug, status, updated_at)
                        VALUES (?, ?, 'seen', ?)
                        ON CONFLICT(person_name, production_slug) DO UPDATE SET status = 'seen', updated_at = excluded.updated_at
                        """,
                        (p_name, mp["slug"], now),
                    )

        return {
            "kind": "ticket",
            "id": ticket_id,
            "title": title,
            "venue": venue,
            "event_date": ev_date,
            "event_time": ev_time,
            "seats_info": seats_info,
            "filename": saved_name,
        }

    # Иначе — это хотелка / анонс со скриншота или поста
    saved_name = ""
    if src_path and src_path.exists():
        ext = src_path.suffix.lower() or ".jpg"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        saved_name = f"shot-{stamp}{ext}"
        shutil.copy2(src_path, SCREENSHOTS_DIR / saved_name)

    title = parsed.get("title") or "Событие из входящих"
    director = parsed.get("director") or "Из входящих / Выставка"
    venue = parsed.get("venue") or "Площадка в Москве"
    ev_date = parsed.get("event_date") or ""
    note = parsed.get("note") or text_message[:200]

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT slug FROM productions WHERE lower(title) = lower(?)",
            (title,),
        ).fetchone()
        if existing:
            linked_slug = existing["slug"]
            conn.execute(
                "UPDATE productions SET status = 'sniper', sniper_enabled = 1, updated_at = ? WHERE slug = ?",
                (now, linked_slug),
            )
        else:
            linked_slug = slugify_title(title)
            conn.execute(
                """
                INSERT INTO productions (
                    slug, title, director, category, venue_id, venue_name, hall_id,
                    official_url, duration, director_hidden_on_site, is_high_demand,
                    note, status, sniper_enabled, default_party, source_type, updated_at
                ) VALUES (?, ?, ?, 'screenshot', 'peter_wolf', ?, 'chamber_center', 'https://afisha.yandex.ru/moscow', '', 0, 1, ?, 'sniper', 1, '[1,2]', 'screenshot', ?)
                """,
                (linked_slug, title, director, venue, note, now),
            )
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ev_date):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        production_slug, event_date, event_time, price_min, price_max,
                        seats_hint, target_seat_offer, is_live_verified, ticket_url
                    ) VALUES (?, ?, ?, 0, 0, 'Добавлено из входящих', '1-й или 5-й ряд по центру', 0, '')
                    """,
                    (linked_slug, ev_date, parsed.get("event_time") or "19:00"),
                )

        cur = conn.execute(
            """
            INSERT INTO screenshots (
                filename, source, extracted_title, extracted_director,
                extracted_venue, extracted_dates, extracted_note,
                linked_slug, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'added_to_hunt', ?)
            """,
            (saved_name, source, title, director, venue, ev_date, note, linked_slug, now),
        )
        shot_id = cur.lastrowid

    return {
        "kind": "wishlist",
        "id": shot_id,
        "filename": saved_name,
        "title": title,
        "director": director,
        "venue": venue,
        "event_date": ev_date,
        "linked_slug": linked_slug,
    }


async def process_screenshot_file(
    src_path: Path,
    source: str = "upload",
    caption_hint: str = "",
    force_kind: str = "auto",
) -> dict[str, Any]:
    return await ingest_incoming_item(
        src_path=src_path,
        text_message=caption_hint,
        source=source,
        force_kind=force_kind,
    )


async def poll_photos_inbox() -> list[dict[str, Any]]:
    ensure_dirs()
    processed_dir = PHOTOS_INBOX_DIR / "processed"
    processed_dir.mkdir(exist_ok=True)

    results: list[dict[str, Any]] = []
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"}
    for p in sorted(PHOTOS_INBOX_DIR.iterdir()):
        if p.is_file() and p.suffix.lower() in allowed and not p.name.startswith("."):
            item = await ingest_incoming_item(src_path=p, source="photos_folder")
            shutil.move(str(p), str(processed_dir / p.name))
            results.append(item)
    return results


def list_screenshots() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM screenshots ORDER BY id DESC LIMIT 50").fetchall()
    return [dict(r) for r in rows]
