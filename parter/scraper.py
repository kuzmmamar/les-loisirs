from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from parter.catalog import BLOCKED_RESELLER_DOMAINS, HALL_GEOMETRY
from parter.db import get_conn, set_setting, sync_liked_directors_discovery

RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

DIRECT_THEATER_DOMAINS = {
    "vakhtangov.ru",
    "mxat.ru",
    "theatreofnations.ru",
    "ramt.ru",
    "teatrpushkin.ru",
    "satirikon.ru",
    "sdart.ru",
    "o3ero.ru",
    "bolshoi.ru",
    "stanmus.ru",
    "okolo.ru",
    "tagankateatr.ru",
    "moscowtyz.ru",
    "vnutrispace.com",
    "mayakovsky.ru",
    "fomenki.ru",
    "sti.ru",
    "teatrcdr.ru",
    "tabakov.ru",
    "mikhalkov12.ru",
    "zaryadye-hall.com",
    "kremlinpalace.org",
    "mariinsky.ru",
    "bdt.spb.ru",
    "eifmanballet.com",
}

OFFICIAL_PARTNER_DOMAINS = {
    "afisha.yandex.ru",
    "ticketland.ru",
    "afisha.ru",
    "kassir.ru",
    "msk.kassir.ru",
    "bigbilet.ru",
    "redkassa.ru",
    "russpass.ru",
    "qtickets.events",
    "moscow.qtickets.events",
    "mos.ru",
    "bilet.mos.ru",
}


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def is_safe_official_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return False
    if host in BLOCKED_RESELLER_DOMAINS:
        return False
    return not any(host.endswith("." + bad) for bad in BLOCKED_RESELLER_DOMAINS)


def infer_year(month: int, explicit_year: str | None, today: date) -> int:
    if explicit_year:
        y = int(explicit_year)
        return y + 2000 if y < 100 else y
    if month < today.month - 1:
        return today.year + 1
    return today.year


def parse_russian_dates_and_prices(text: str, today: date | None = None) -> tuple[list[tuple[str, str]], int, int]:
    today = today or date.today()
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # Паттерн Вахтангова / МХТ / РАМТа: «10 декабря, четверг, 19:00» или «9 января 2027, суббота, 19:00» или «14 декабря, Пн ∙ 19:00»
    pattern = re.compile(
        r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
        r"(?:\s+(20\d{2}))?"
        r"[^\n\d]{0,32}?(\d{1,2}:\d{2})\b",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        day = int(m.group(1))
        month = RU_MONTHS[m.group(2).lower()]
        year = infer_year(month, m.group(3), today)
        tm = m.group(4)
        try:
            dt = date(year, month, day)
        except ValueError:
            continue
        if dt < today:
            continue
        # Не берём исторические даты или слишком далёкие (> 240 дней)
        if (dt - today).days > 240:
            continue
        iso = dt.isoformat()
        if (iso, tm) not in seen:
            seen.add((iso, tm))
            found.append((iso, tm))

    # Доп. паттерн для камерных сайтов вроде o3ero.ru: «18:00 / 21:00 ... 19.07, вс» или «19.12, сб 19:00»
    dot_pattern = re.compile(
        r"\b(\d{1,2})\.(\d{2})\s*,\s*(?:пн|вт|ср|чт|пт|сб|вс)\b",
        re.IGNORECASE,
    )
    times_nearby = re.findall(r"\b([12]\d:[0-5]\d)\b", text)
    default_tm = times_nearby[0] if times_nearby else "19:00"
    for m in dot_pattern.finditer(text):
        day, month = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12):
            continue
        year = infer_year(month, None, today)
        try:
            dt = date(year, month, day)
        except ValueError:
            continue
        if dt < today or (dt - today).days > 240:
            continue
        iso = dt.isoformat()
        if (iso, default_tm) not in seen:
            seen.add((iso, default_tm))
            found.append((iso, default_tm))

    price_min, price_max = 0, 0
    price_pat = re.search(
        r"от\s*(\d[\d\s]{2,6})\s*(?:₽|руб)?\s*до\s*(\d[\d\s]{2,6})\s*(?:₽|руб)",
        text,
        re.IGNORECASE,
    )
    if price_pat:
        price_min = int(re.sub(r"\D", "", price_pat.group(1)))
        price_max = int(re.sub(r"\D", "", price_pat.group(2)))

    return found, price_min, price_max


async def scan_official_theaters() -> dict[str, int | str]:
    """Синхронизирует постановки любимых режиссёров по Москве и опрашивает официальные страницы театров."""
    radar_added = sync_liked_directors_discovery()
    with get_conn() as conn:
        prods = conn.execute(
            "SELECT slug, title, hall_id, official_url FROM productions WHERE status != 'skip'"
        ).fetchall()

    checked = 0
    live_hits = 0
    upserted_events = 0
    today = date.today()

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=10.0) as client:
        for p in prods:
            url = p["official_url"]
            host = (urlparse(url).hostname or "").lower().removeprefix("www.")
            if not is_safe_official_url(url) or host not in DIRECT_THEATER_DOMAINS:
                continue
            checked += 1
            try:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                clean_text = soup.get_text(" ", strip=True)
                dates, p_min, p_max = parse_russian_dates_and_prices(clean_text, today=today)
                if not dates:
                    continue
                live_hits += 1
                hall = HALL_GEOMETRY.get(p["hall_id"], HALL_GEOMETRY["chamber_center"])
                top_target = hall["targets"][0]
                offer = f"{top_target['zone']}, ряд {top_target['row']}, места {top_target['seats']}"

                with get_conn() as conn:
                    for ev_date, ev_time in dates:
                        conn.execute(
                            """
                            INSERT INTO events (
                                production_slug, event_date, event_time, price_min, price_max,
                                seats_hint, target_seat_offer, is_live_verified, ticket_url
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                            ON CONFLICT(production_slug, event_date, event_time) DO UPDATE SET
                                is_live_verified = 1,
                                price_min = CASE WHEN excluded.price_min > 0 THEN excluded.price_min ELSE events.price_min END,
                                price_max = CASE WHEN excluded.price_max > 0 THEN excluded.price_max ELSE events.price_max END
                            """,
                            (
                                p["slug"],
                                ev_date,
                                ev_time,
                                p_min,
                                p_max,
                                "Проверено напрямую на официальном сайте театра",
                                offer,
                                url,
                            ),
                        )
                        upserted_events += 1
            except Exception:
                # Если площадка временно закрыла соединение или под защитой — сохраняем текущие данные без падения
                continue

    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    radar_note = f" · найдено по режиссёрам: +{len(radar_added)}" if radar_added else ""
    summary = f"Проверено {checked} офиц. страниц · подтверждено {live_hits} спектаклей ({upserted_events} показов){radar_note}"
    set_setting("last_scan_at", stamp)
    set_setting("last_scan_summary", summary)
    with get_conn() as conn:
        conn.execute("UPDATE sources SET last_checked_at = ? WHERE active = 1", (stamp,))
    return {
        "checked": checked,
        "live_hits": live_hits,
        "upserted_events": upserted_events,
        "timestamp": stamp,
        "summary": summary,
    }
