from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from parter.catalog import HALL_GEOMETRY
from parter.db import get_conn, get_settings_dict, list_companions
from parter.planner import estimate_target_ticket_price


def create_checkout_order(
    event_id: int,
    party_ids: list[int],
    ticket_count: int | None = None,
    mode: str = "manual_approve",
) -> dict[str, Any]:
    settings = get_settings_dict()
    max_price = int(settings.get("max_price_per_ticket", "60000") or 60000)
    payment_method = settings.get("payment_method", "card_3ds") or "card_3ds"
    card_masked = settings.get("card_masked", "") or "Карта РФ •••• (не привязана — демо до 3D-Secure)"
    mos_id_masked = settings.get("mos_id_masked", "")

    companions = list_companions()
    comp_map = {c["id"]: c for c in companions}
    chosen_people = [comp_map[cid] for cid in party_ids if cid in comp_map]
    if not chosen_people and companions:
        chosen_people = [companions[0]]

    viewer_labels = []
    for p in chosen_people:
        fio = (p.get("full_name") or "").strip()
        if fio == "Основной пользователь":
            fio = ""
        doc_m = (p.get("doc_masked") or "").strip()
        if fio and doc_m and not doc_m.startswith("Не заполнен") and doc_m != "Без документа":
            viewer_labels.append(f"{p['name']} ({fio}, {doc_m})")
        elif fio:
            viewer_labels.append(f"{p['name']} ({fio})")
        else:
            viewer_labels.append(f"{p['name']} (тест — без ФИО/паспорта)")
    viewers_summary = "; ".join(viewer_labels)

    with get_conn() as conn:
        ev = conn.execute(
            """
            SELECT e.*, p.title, p.venue_id, p.venue_name, p.hall_id, p.official_url
            FROM events e
            JOIN productions p ON p.slug = e.production_slug
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
        if not ev:
            raise ValueError("Показ не найден")

        if mode == "auto_sniper":
            away_hit = conn.execute(
                "SELECT note FROM away_periods WHERE is_primary = 1 AND start_date <= ? AND end_date >= ? LIMIT 1",
                (ev["event_date"], ev["event_date"]),
            ).fetchone()
            if away_hit:
                raise ValueError(f"На дату {ev['event_date']} отмечен отъезд ({away_hit['note']}) — автопокупка остановлена")

        hall = HALL_GEOMETRY.get(ev["hall_id"], HALL_GEOMETRY["chamber_center"])
        top = hall["targets"][0]
        count = max(1, min(3, ticket_count or len(chosen_people)))
        if count == 1:
            seat_numbers = "1 билет · место 12 (середина)"
        elif count == 2:
            seat_numbers = "2 билета рядом · места 12, 13"
        else:
            seat_numbers = "3 билета рядом · места 11, 12, 13"
        seat_selection = f"{top['zone']} · ряд {top['row']} · {seat_numbers}"

        if ev["venue_id"] == "okolo":
            channel_label = f"Mos ID ({mos_id_masked})" if mos_id_masked else "Касса bilet.mos.ru (нужен Mos ID)"
        else:
            channel_label = "Прямая касса театра (0%) · резерв: Яндекс Афиша / Ticketland"

        unit_price = estimate_target_ticket_price(ev["price_min"], ev["price_max"])
        if unit_price > max_price:
            status = "needs_price_override"
            bank_hint = f"Цена {unit_price:,} ₽ выше лимита {max_price:,} ₽ — подтвердите превышение ({viewers_summary})".replace(",", " ")
        else:
            status = "awaiting_bank_push"
            if payment_method == "sbp":
                pay_label = "СБП (пуш в приложении банка)"
            else:
                pay_label = f"{card_masked} → ожидание пуша / СМС 3D-Secure"
            bank_hint = (
                f"Канал: {channel_label} · Зрители: {viewers_summary} · Оплата: {pay_label}. "
                f"Корзина держится 15 мин. Свайп ← отменит бронь."
            )

        now = datetime.now()
        hold_until = (now + timedelta(minutes=15)).strftime("%H:%M")
        party_names = " + ".join(p["name"] for p in chosen_people)
        total_price = unit_price * count

        cur = conn.execute(
            """
            INSERT INTO orders (
                event_id, production_slug, production_title, venue_name,
                event_date, event_time, party_names, ticket_count,
                seat_selection, price_per_ticket, total_price,
                mode, status, bank_hint, hold_expires_at, official_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                ev["production_slug"],
                ev["title"],
                ev["venue_name"],
                ev["event_date"],
                ev["event_time"],
                party_names,
                count,
                seat_selection,
                unit_price,
                total_price,
                mode,
                status,
                bank_hint,
                hold_until,
                ev["official_url"],
                now.isoformat(timespec="seconds"),
            ),
        )
        order_id = cur.lastrowid
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row)


def confirm_order_payment(order_id: int, three_ds_code: str = "") -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            raise ValueError("Заказ не найден")
        conn.execute(
            """
            UPDATE orders
            SET status = 'paid',
                three_ds_code = ?,
                bank_hint = 'Подтверждено в банке · билет добавлен в ваш кошелёк и календарь'
            WHERE id = ?
            """,
            ("confirmed" if not three_ds_code else "***", order_id),
        )
        conn.execute(
            """
            INSERT INTO wallet_tickets (
                title, venue_name, event_date, event_time, seats_info,
                who_goes, sender_name, order_number, filename, file_kind, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'Автопокупка', ?, '', 'order', 'auto_checkout', ?)
            """,
            (
                order["production_title"],
                order["venue_name"],
                order["event_date"],
                order["event_time"],
                order["seat_selection"],
                order["party_names"],
                f"#{order_id}",
                now,
            ),
        )
        conn.execute(
            "UPDATE productions SET status = 'seen', sniper_enabled = 0, updated_at = ? WHERE slug = ?",
            (now, order["production_slug"]),
        )
        updated = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(updated)


def cancel_order(order_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE orders SET status = 'cancelled', bank_hint = 'Бронь отменена' WHERE id = ?", (order_id,))


def list_orders() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 30").fetchall()
    return [dict(r) for r in rows]


def list_wallet_tickets() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wallet_tickets ORDER BY event_date ASC, event_time ASC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_wallet_ticket(ticket_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM wallet_tickets WHERE id = ?", (ticket_id,))
