from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from parter.catalog import HALL_GEOMETRY, OFFICIAL_VENUES
from parter.db import (
    get_conn,
    get_settings_dict,
    list_away_periods,
    list_companions,
    list_person_rules,
    list_tracked_creators,
    production_matches_rule,
    score_production_for_person,
    split_director_names,
    sync_liked_directors_discovery,
)

RU_WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
RU_WEEKDAYS_FULL = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]
RU_MONTH_GEN = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]
RU_MONTH_SHORT = [
    "",
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
]


def calc_end_time(start_time: str, duration_str: str) -> str:
    """Считает ориентировочное время окончания спектакля (например, 19:00 -> ~22:30)."""
    try:
        hh, mm = [int(x) for x in start_time.split(":")[:2]]
    except Exception:
        return ""
    hrs_m = re.search(r"(\d+)\s*ч", duration_str or "")
    min_m = re.search(r"(\d+)\s*м", duration_str or "")
    total_mins = (int(hrs_m.group(1)) * 60 if hrs_m else 150) + (int(min_m.group(1)) if min_m else 0)
    dt = datetime(2026, 1, 1, hh, mm) + timedelta(minutes=total_mins)
    return dt.strftime("%H:%M")


def format_human_date(iso_date: str) -> dict[str, Any]:
    try:
        dt = date.fromisoformat(iso_date)
    except ValueError:
        dt = date.today()
    today = date.today()
    delta = (dt - today).days
    weekday_short = RU_WEEKDAYS_SHORT[dt.weekday()]
    weekday_full = RU_WEEKDAYS_FULL[dt.weekday()]
    is_weekend = dt.weekday() >= 4  # Пт, Сб, Вс
    short_label = f"{dt.day} {RU_MONTH_SHORT[dt.month]}"
    full_label = f"{dt.day} {RU_MONTH_GEN[dt.month]}"
    if abs(delta) > 300:
        full_label += f" {dt.year}"
    if delta == 0:
        rel = "Сегодня"
    elif delta == 1:
        rel = "Завтра"
    elif delta > 1:
        rel = f"через {delta} дн."
    else:
        rel = "Прошло"
    return {
        "label": short_label,
        "full_date": full_label,
        "weekday": weekday_short,
        "weekday_full": weekday_full,
        "is_weekend": is_weekend,
        "days_away": delta,
        "relative": rel,
        "day_num": dt.day,
    }


def estimate_target_ticket_price(price_min: int, price_max: int) -> int:
    if price_max > 0:
        return price_max
    if price_min > 0:
        return price_min * 3
    return 8000


def _season_end(day: date) -> date:
    """Театральный сезон: 1 сентября — 31 июля."""
    year = day.year + 1 if day.month >= 8 else day.year
    return date(year, 7, 31)


def build_weekly_proposals(person_name: str = "А") -> dict[str, Any]:
    person_name = (person_name or "А").strip()
    if person_name in ("К", "А+1", "+1"):
        person_name = "+1"
    else:
        person_name = "А"
    partner_name = "+1" if person_name == "А" else "А"
    settings = get_settings_dict()
    max_price = int(settings.get("max_price_per_ticket", "60000") or 60000)
    companions = list_companions()
    comp_by_id = {c["id"]: c for c in companions}
    comp_by_name = {c["name"]: c for c in companions}

    rules = list_person_rules(person_name)
    away_periods = list_away_periods()

    today = date.today()
    today_iso = today.isoformat()
    current_monday = today - timedelta(days=today.weekday())

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                e.id AS event_id,
                e.production_slug,
                e.event_date,
                e.event_time,
                e.price_min,
                e.price_max,
                e.seats_hint,
                e.target_seat_offer,
                e.is_live_verified,
                e.ticket_url,
                p.title,
                p.director,
                p.stage_director,
                p.is_liked,
                p.discovered_by_director,
                p.category,
                p.venue_id,
                p.venue_name,
                p.hall_id,
                p.official_url,
                p.duration,
                p.director_hidden_on_site,
                p.is_high_demand,
                p.note,
                pd.status AS status,
                p.sniper_enabled,
                p.default_party
            FROM events e
            JOIN productions p ON p.slug = e.production_slug
            LEFT JOIN person_decisions pd
              ON pd.production_slug = p.slug AND pd.person_name = ?
            WHERE e.event_date >= ?
              AND COALESCE(pd.status, 'inbox') IN ('want', 'sniper')
            ORDER BY e.event_date ASC, e.event_time ASC
            """,
            (person_name, today_iso),
        ).fetchall()

        partner_rows = conn.execute(
            "SELECT production_slug, status FROM person_decisions WHERE person_name = ?",
            (partner_name,),
        ).fetchall()
        partner_dec = {r["production_slug"]: r["status"] for r in partner_rows}

        w_rows = conn.execute(
            "SELECT feature_key, score FROM person_taste_weights WHERE person_name = ?",
            (person_name,),
        ).fetchall()
        weights_map = {r["feature_key"]: float(r["score"]) for r in w_rows}

        wallet_rows = conn.execute(
            "SELECT * FROM wallet_tickets WHERE event_date >= ? ORDER BY event_date ASC, event_time ASC",
            (current_monday.isoformat(),),
        ).fetchall()

    def get_away_for_date(iso_str: str) -> list[dict[str, Any]]:
        return [a for a in away_periods if a["start_date"] <= iso_str <= a["end_date"]]

    # Индексируем купленные билеты по дате
    booked_by_date: dict[str, list[dict[str, Any]]] = {}
    for w in wallet_rows:
        wd = dict(w)
        wd.update(format_human_date(wd["event_date"]))
        booked_by_date.setdefault(wd["event_date"], []).append(wd)

    # Индексируем предложения по дате для выбранного человека (person_name)
    events_by_date: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        item = dict(r)
        if any(production_matches_rule(item, rule) for rule in rules):
            continue

        day_away = get_away_for_date(item["event_date"])
        away_names = {a["person_name"] for a in day_away}
        # Если самого выбранного пользователя (А или К) нет в Москве — не показываем показ
        if person_name in away_names:
            continue

        dt_info = format_human_date(item["event_date"])
        hall = HALL_GEOMETRY.get(item["hall_id"], HALL_GEOMETRY["chamber_center"])
        venue_meta = OFFICIAL_VENUES.get(item["venue_id"], {})

        # Если второй человек в этот день в отъезде — ставим 1 билет на выбранного пользователя
        if partner_name in away_names and person_name in comp_by_name:
            party_people = [comp_by_name[person_name]]
        else:
            party_ids = json.loads(item["default_party"] or "[1,2]")
            party_people = [comp_by_id[cid] for cid in party_ids if cid in comp_by_id]
            if not party_people and companions:
                party_people = [companions[0]]

        est_price = estimate_target_ticket_price(item["price_min"], item["price_max"])
        end_time = calc_end_time(item["event_time"], item["duration"])
        partner_wants = partner_dec.get(item["production_slug"]) in ("want", "sniper")
        taste_score = score_production_for_person(item, weights_map) + (0.6 if partner_wants else 0.0)

        stage_dir = item.get("stage_director") or item["director"]
        director_role = "Хореограф" if item.get("category") == "ballet" else "Режиссёр"
        enriched = {
            **item,
            "stage_director": stage_dir,
            "director_role": director_role,
            "date_human": dt_info["label"],
            "full_date": dt_info["full_date"],
            "weekday": dt_info["weekday"],
            "weekday_full": dt_info["weekday_full"],
            "is_weekend": dt_info["is_weekend"],
            "days_away": dt_info["days_away"],
            "relative": dt_info["relative"],
            "end_time": end_time,
            "time_range": f"{item['event_time']}–{end_time}" if end_time else item["event_time"],
            "hall_summary": hall["summary"],
            "seat_targets": hall["targets"],
            "sales_rule": venue_meta.get("sales_rule", ""),
            "party_ids": [p["id"] for p in party_people],
            "party_label": " + ".join(p["name"] for p in party_people),
            "ticket_count": len(party_people),
            "est_ticket_price": est_price,
            "est_total_price": est_price * len(party_people),
            "over_budget": est_price > max_price,
            "partner_wants": partner_wants,
            "partner_name": partner_name,
            "taste_score": taste_score,
            "other_away_names": [n for n in away_names if n != person_name],
        }
        events_by_date.setdefault(item["event_date"], []).append(enriched)

    for iso_k in events_by_date:
        events_by_date[iso_k].sort(key=lambda x: (x["event_time"], x.get("title") or ""))

    # Недели от текущего понедельника до конца театрального сезона (31 июля)
    horizon_end = _season_end(today)
    horizon_monday = horizon_end - timedelta(days=horizon_end.weekday())
    week_count = ((horizon_monday - current_monday).days // 7) + 1
    weeks: list[dict[str, Any]] = []
    for w_idx in range(week_count):
        mon = current_monday + timedelta(days=7 * w_idx)
        sun = mon + timedelta(days=6)

        if mon.month == sun.month:
            range_title = f"{mon.day}–{sun.day} {RU_MONTH_GEN[sun.month]}"
        else:
            range_title = f"{mon.day} {RU_MONTH_SHORT[mon.month]} – {sun.day} {RU_MONTH_SHORT[sun.month]}"

        if w_idx == 0:
            week_badge = "Эта неделя"
        elif w_idx == 1:
            week_badge = "Следующая неделя"
        else:
            week_badge = f"Неделя +{w_idx}"

        days: list[dict[str, Any]] = []
        total_offers = 0
        total_booked = 0
        total_away = 0

        for d_idx in range(7):
            cur_dt = mon + timedelta(days=d_idx)
            iso = cur_dt.isoformat()
            info = format_human_date(iso)
            day_booked = booked_by_date.get(iso, [])
            # Оба (А и К) видят отъезды друг друга в календаре
            day_away = get_away_for_date(iso)
            is_self_away = any(a["person_name"] == person_name for a in day_away)
            # На даты, когда выбранного человека нет, спектакли не показываем
            day_offers = [] if is_self_away else (events_by_date.get(iso, []) if cur_dt >= today else [])

            total_offers += len(day_offers)
            total_booked += len(day_booked)
            if day_away:
                total_away += 1

            days.append(
                {
                    "iso_date": iso,
                    "is_past": cur_dt < today,
                    "is_today": cur_dt == today,
                    "day_num": cur_dt.day,
                    "month_short": RU_MONTH_SHORT[cur_dt.month],
                    "full_date": info["full_date"],
                    "weekday_short": info["weekday"],
                    "weekday_full": info["weekday_full"],
                    "is_weekend": info["is_weekend"],
                    "relative": info["relative"],
                    "away_items": day_away,
                    "is_primary_away": is_self_away,
                    "booked_tickets": day_booked,
                    "offers": day_offers,
                }
            )

        weeks.append(
            {
                "week_index": w_idx,
                "start_date": mon.isoformat(),
                "end_date": sun.isoformat(),
                "range_title": range_title,
                "week_badge": week_badge,
                "total_offers": total_offers,
                "total_booked": total_booked,
                "total_away": total_away,
                "days": days,
            }
        )

    rep_for_person = list_repertoire(person_name)
    swiped_want_count = sum(1 for r in rep_for_person if r["status"] in ("want", "sniper"))
    inbox_count = sum(1 for r in rep_for_person if r["status"] == "inbox")

    return {
        "active_person": person_name,
        "today_iso": today_iso,
        "max_price_per_ticket": max_price,
        "seat_rule": settings.get("seat_rule", ""),
        "last_scan_at": settings.get("last_scan_at", ""),
        "last_scan_summary": settings.get("last_scan_summary", ""),
        "swiped_want_count": swiped_want_count,
        "inbox_count": inbox_count,
        "weeks": weeks,
    }


def list_repertoire(person_name: str = "А") -> list[dict[str, Any]]:
    person_name = (person_name or "А").strip()
    if person_name in ("К", "А+1", "+1"):
        person_name = "+1"
    else:
        person_name = "А"
    partner_name = "+1" if person_name == "А" else "А"
    today_iso = date.today().isoformat()
    sync_liked_directors_discovery()
    rules = list_person_rules(person_name)
    away_periods = list_away_periods()

    with get_conn() as conn:
        prods = conn.execute("SELECT * FROM productions").fetchall()
        dec_rows = conn.execute(
            "SELECT person_name, production_slug, status FROM person_decisions WHERE person_name IN (?, ?)",
            (person_name, partner_name),
        ).fetchall()
        w_rows = conn.execute(
            "SELECT feature_key, score FROM person_taste_weights WHERE person_name = ?",
            (person_name,),
        ).fetchall()
        events = conn.execute(
            "SELECT * FROM events WHERE event_date >= ? ORDER BY event_date ASC",
            (today_iso,),
        ).fetchall()

    my_dec: dict[str, str] = {}
    partner_dec: dict[str, str] = {}
    for r in dec_rows:
        if r["person_name"] == person_name:
            my_dec[r["production_slug"]] = r["status"]
        else:
            partner_dec[r["production_slug"]] = r["status"]

    weights_map = {r["feature_key"]: float(r["score"]) for r in w_rows}

    ev_by_slug: dict[str, list[dict[str, Any]]] = {}
    raw_ev_count: dict[str, int] = {}

    for e in events:
        ed = dict(e)
        slug = ed["production_slug"]
        raw_ev_count[slug] = raw_ev_count.get(slug, 0) + 1
        day_away = [a for a in away_periods if a["start_date"] <= ed["event_date"] <= a["end_date"]]
        is_self_away = any(a["person_name"] == person_name for a in day_away)
        # На даты, когда выбранного человека (А или К) нет в Москве — не показываем показ
        if is_self_away:
            continue

        other_away = [a["person_name"] for a in day_away if a["person_name"] != person_name]
        ed["event_id"] = ed["id"]
        ed["is_primary_away"] = False
        ed["other_away_names"] = other_away
        ed["other_away_label"] = f"{', '.join(other_away)} нет в Мск" if other_away else ""
        ed.update(format_human_date(ed["event_date"]))
        ed["est_ticket_price"] = estimate_target_ticket_price(ed["price_min"], ed["price_max"])
        hint_low = (ed.get("seats_hint") or "").lower()
        ed["has_tickets"] = not any(
            w in hint_low for w in ("билетов нет", "раскуплен", "sold out", "старт продаж")
        )
        ev_by_slug.setdefault(slug, []).append(ed)

    result: list[dict[str, Any]] = []
    for p in prods:
        d = dict(p)
        # Применяем персональные правила исключений (например, для А скрыта опера в Большом)
        if any(production_matches_rule(d, rule) for rule in rules):
            continue

        evs = ev_by_slug.get(d["slug"], [])
        # Если все известные даты спектакля выпадают на отъезд выбранного человека — скрываем
        if raw_ev_count.get(d["slug"], 0) > 0 and not evs:
            continue

        d["status"] = my_dec.get(d["slug"], "inbox")
        p_st = partner_dec.get(d["slug"], "inbox")
        d["partner_status"] = p_st
        d["partner_wants"] = p_st in ("want", "sniper")
        d["both_seen"] = (d["status"] == "seen") and (p_st == "seen")
        d["partner_name"] = partner_name
        d["stage_director"] = d.get("stage_director") or d["director"]
        d["director_role"] = "Хореограф" if d.get("category") == "ballet" else "Режиссёр"
        d["taste_score"] = score_production_for_person(d, weights_map) + (0.6 if d["partner_wants"] else 0.0)

        hall = HALL_GEOMETRY.get(d["hall_id"], HALL_GEOMETRY["chamber_center"])
        d["hall_summary"] = hall["summary"]
        d["seat_targets"] = hall["targets"]
        d["party_ids"] = json.loads(d["default_party"] or "[1,2]")
        for ev in evs:
            ev["end_time"] = calc_end_time(ev["event_time"], d["duration"])
        evs.sort(
            key=lambda x: (
                0 if x.get("has_tickets", True) else 1,
                x["event_date"],
                x["event_time"],
            )
        )
        d["upcoming_events"] = evs
        result.append(d)

    def _nearest_show(item: dict[str, Any]) -> tuple[str, str]:
        evs = item.get("upcoming_events") or []
        if not evs:
            return ("9999-12-31", "99:99")
        first = min(
            evs,
            key=lambda e: (e.get("event_date") or "9999-12-31", e.get("event_time") or "99:99"),
        )
        return (first.get("event_date") or "9999-12-31", first.get("event_time") or "99:99")

    result.sort(key=lambda x: (*_nearest_show(x), x.get("title") or ""))
    return result


def build_liked_overview(person_name: str = "А") -> dict[str, Any]:
    """
    Данные для 3-й вкладки «Нравятся»:
    строго только то, что свайпнуто вправо («Хочу»), ранее добавлено или отмечено лайком после спектакля.
    """
    person_name = "+1" if (person_name or "А").strip() in ("К", "А+1", "+1") else "А"
    sync_liked_directors_discovery()
    today_iso = date.today().isoformat()

    with get_conn() as conn:
        prods = [dict(r) for r in conn.execute("SELECT * FROM productions").fetchall()]
        dec_rows = conn.execute("SELECT person_name, production_slug, status FROM person_decisions").fetchall()
        like_rows = conn.execute("SELECT person_name, production_slug FROM person_likes").fetchall()
        manual_rows = [dict(r) for r in conn.execute("SELECT * FROM manual_liked_plays ORDER BY id DESC").fetchall()]
        ev_rows = conn.execute(
            "SELECT * FROM events WHERE event_date >= ? ORDER BY event_date ASC, event_time ASC",
            (today_iso,),
        ).fetchall()

    dec_by_slug: dict[str, dict[str, str]] = {}
    for r in dec_rows:
        dec_by_slug.setdefault(r["production_slug"], {})[r["person_name"]] = r["status"]

    likes_by_person: dict[str, set[str]] = {"А": set(), "+1": set()}
    for r in like_rows:
        likes_by_person.setdefault(r["person_name"], set()).add(r["production_slug"])

    ev_by_slug: dict[str, list[dict[str, Any]]] = {}
    for e in ev_rows:
        ed = dict(e)
        ed.update(format_human_date(ed["event_date"]))
        ev_by_slug.setdefault(ed["production_slug"], []).append(ed)

    liked_plays: list[dict[str, Any]] = []
    my_liked_slugs = likes_by_person.get(person_name, set())

    for p in prods:
        slug = p["slug"]
        decs = dec_by_slug.get(slug, {})
        my_status = decs.get(person_name, "inbox")
        st_a = decs.get("А", "inbox")
        st_p1 = decs.get("+1", "inbox")

        swiped_right = my_status in ("want", "sniper")
        explicitly_liked = slug in my_liked_slugs
        if not (swiped_right or explicitly_liked):
            continue

        seen_by = [who for who, st in (("А", st_a), ("+1", st_p1)) if st == "seen"]
        stage_dir = p.get("stage_director") or p["director"]
        role_label = "Хореограф" if p.get("category") == "ballet" else "Режиссёр"
        evs = ev_by_slug.get(slug, [])

        liked_plays.append(
            {
                "kind": "catalog",
                "slug": slug,
                "title": p["title"],
                "venue_id": p["venue_id"],
                "venue_name": p["venue_name"],
                "category": p["category"],
                "stage_director": stage_dir,
                "director_role": role_label,
                "swiped_right": swiped_right,
                "explicitly_liked": explicitly_liked,
                "seen_by": seen_by,
                "both_seen": len(seen_by) == 2,
                "upcoming_events": evs[:3],
            }
        )

    for m in manual_rows:
        role_label = "Хореограф" if m.get("category") == "ballet" else "Режиссёр"
        liked_plays.append(
            {
                "kind": "manual",
                "id": m["id"],
                "slug": f"manual-{m['id']}",
                "title": m["title"],
                "venue_id": "manual",
                "venue_name": m["venue_name"] or "Театр Москвы",
                "category": m["category"],
                "stage_director": m["stage_director"] or "",
                "director_role": role_label,
                "swiped_right": False,
                "explicitly_liked": True,
                "seen_by": [person_name],
                "both_seen": False,
                "upcoming_events": [],
            }
        )

    liked_plays.sort(
        key=lambda x: (
            ((x.get("upcoming_events") or [{}])[0].get("event_date") or "9999-12-31"),
            ((x.get("upcoming_events") or [{}])[0].get("event_time") or "99:99"),
            x["title"],
        )
    )
    tracked_creators = list_tracked_creators(person_name)

    return {
        "tracked_creators": tracked_creators,
        "liked_plays": liked_plays,
    }

