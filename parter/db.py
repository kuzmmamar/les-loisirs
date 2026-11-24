from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet

from parter.catalog import (
    DIRECTOR_MOSCOW_DISCOVERY,
    GOLDEN_MASK_LAUREATE_SLUGS,
    HALL_GEOMETRY,
    OFFICIAL_VENUES,
    SEED_PRODUCTIONS,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "parter.db"
KEY_PATH = DATA_DIR / ".vault.key"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
TICKETS_DIR = DATA_DIR / "tickets"
PHOTOS_INBOX_DIR = DATA_DIR / "photos_inbox"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    PHOTOS_INBOX_DIR.mkdir(parents=True, exist_ok=True)


def get_cipher() -> Fernet:
    ensure_dirs()
    env_key = os.environ.get("PARTER_VAULT_KEY")
    if env_key:
        return Fernet(env_key.encode("utf-8"))
    if not KEY_PATH.exists():
        key = Fernet.generate_key()
        KEY_PATH.write_bytes(key)
        os.chmod(KEY_PATH, 0o600)
    return Fernet(KEY_PATH.read_bytes().strip())


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return get_cipher().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    return get_cipher().decrypt(token.encode("utf-8")).decode("utf-8")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS companions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'companion',
                full_name TEXT DEFAULT '',
                doc_type TEXT DEFAULT 'Паспорт РФ',
                doc_encrypted TEXT DEFAULT '',
                doc_masked TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                is_self INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS productions (
                slug TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                director TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'theatre',
                venue_id TEXT NOT NULL,
                venue_name TEXT NOT NULL,
                hall_id TEXT NOT NULL,
                official_url TEXT NOT NULL,
                duration TEXT DEFAULT '',
                director_hidden_on_site INTEGER NOT NULL DEFAULT 0,
                is_high_demand INTEGER NOT NULL DEFAULT 0,
                note TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'want',
                sniper_enabled INTEGER NOT NULL DEFAULT 0,
                default_party TEXT NOT NULL DEFAULT '[1,2]',
                source_type TEXT NOT NULL DEFAULT 'catalog',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_slug TEXT NOT NULL REFERENCES productions(slug) ON DELETE CASCADE,
                event_date TEXT NOT NULL,
                event_time TEXT NOT NULL DEFAULT '19:00',
                price_min INTEGER DEFAULT 0,
                price_max INTEGER DEFAULT 0,
                seats_hint TEXT DEFAULT '',
                target_seat_offer TEXT DEFAULT '',
                is_live_verified INTEGER NOT NULL DEFAULT 0,
                sales_open_at TEXT DEFAULT '',
                ticket_url TEXT DEFAULT '',
                UNIQUE(production_slug, event_date, event_time)
            );

            CREATE TABLE IF NOT EXISTS companion_seen (
                companion_id INTEGER NOT NULL REFERENCES companions(id) ON DELETE CASCADE,
                production_slug TEXT NOT NULL REFERENCES productions(slug) ON DELETE CASCADE,
                PRIMARY KEY (companion_id, production_slug)
            );

            CREATE TABLE IF NOT EXISTS screenshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'upload',
                extracted_title TEXT DEFAULT '',
                extracted_director TEXT DEFAULT '',
                extracted_venue TEXT DEFAULT '',
                extracted_dates TEXT DEFAULT '',
                extracted_note TEXT DEFAULT '',
                linked_slug TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'processed',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                handle_or_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                last_checked_at TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER REFERENCES events(id),
                production_slug TEXT NOT NULL,
                production_title TEXT NOT NULL,
                venue_name TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_time TEXT NOT NULL,
                party_names TEXT NOT NULL,
                ticket_count INTEGER NOT NULL,
                seat_selection TEXT NOT NULL,
                price_per_ticket INTEGER NOT NULL,
                total_price INTEGER NOT NULL,
                mode TEXT NOT NULL DEFAULT 'manual_approve',
                status TEXT NOT NULL DEFAULT 'awaiting_bank_push',
                bank_hint TEXT DEFAULT '',
                hold_expires_at TEXT DEFAULT '',
                three_ds_code TEXT DEFAULT '',
                official_url TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                venue_name TEXT DEFAULT '',
                event_date TEXT NOT NULL,
                event_time TEXT NOT NULL DEFAULT '19:00',
                seats_info TEXT DEFAULT '',
                who_goes TEXT DEFAULT 'А + А+1',
                sender_name TEXT DEFAULT '',
                order_number TEXT DEFAULT '',
                filename TEXT DEFAULT '',
                file_kind TEXT DEFAULT 'pdf',
                source TEXT DEFAULT 'upload',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS processed_emails (
                uid_key TEXT PRIMARY KEY,
                message_id TEXT DEFAULT '',
                subject TEXT DEFAULT '',
                sender TEXT DEFAULT '',
                received_at TEXT DEFAULT '',
                kind TEXT DEFAULT 'ignored',
                ticket_id INTEGER DEFAULT 0,
                processed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS away_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_name TEXT NOT NULL DEFAULT 'А',
                is_primary INTEGER NOT NULL DEFAULT 1,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                note TEXT DEFAULT 'Нет в Москве',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS person_decisions (
                person_name TEXT NOT NULL,
                production_slug TEXT NOT NULL REFERENCES productions(slug) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'inbox',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (person_name, production_slug)
            );

            CREATE TABLE IF NOT EXISTS person_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_name TEXT NOT NULL,
                rule_kind TEXT NOT NULL,
                rule_value TEXT NOT NULL,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(person_name, rule_kind, rule_value)
            );

            CREATE TABLE IF NOT EXISTS person_taste_weights (
                person_name TEXT NOT NULL,
                feature_key TEXT NOT NULL,
                feature_label TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0.0,
                likes INTEGER NOT NULL DEFAULT 0,
                skips INTEGER NOT NULL DEFAULT 0,
                seen_cnt INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (person_name, feature_key)
            );

            CREATE TABLE IF NOT EXISTS manual_liked_plays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                stage_director TEXT DEFAULT '',
                venue_name TEXT DEFAULT '',
                category TEXT DEFAULT 'theatre',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS person_likes (
                person_name TEXT NOT NULL,
                production_slug TEXT NOT NULL REFERENCES productions(slug) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (person_name, production_slug)
            );

            CREATE TABLE IF NOT EXISTS post_show_reviews (
                item_key TEXT NOT NULL,
                person_name TEXT NOT NULL,
                production_slug TEXT DEFAULT '',
                title TEXT NOT NULL,
                venue_name TEXT DEFAULT '',
                stage_director TEXT DEFAULT '',
                event_date TEXT DEFAULT '',
                verdict TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (item_key, person_name)
            );

            CREATE TABLE IF NOT EXISTS tracked_creators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_name TEXT NOT NULL DEFAULT 'А',
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'Режиссёр',
                source_note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(person_name, name)
            );
            """
        )

        # Мягкая миграция колонок productions (режиссёр/хореограф, флаг «нравится», источник радара, Золотая Маска)
        prod_cols = {r["name"] for r in conn.execute("PRAGMA table_info(productions)").fetchall()}
        if "stage_director" not in prod_cols:
            conn.execute("ALTER TABLE productions ADD COLUMN stage_director TEXT DEFAULT ''")
        if "is_liked" not in prod_cols:
            conn.execute("ALTER TABLE productions ADD COLUMN is_liked INTEGER NOT NULL DEFAULT 0")
        if "discovered_by_director" not in prod_cols:
            conn.execute("ALTER TABLE productions ADD COLUMN discovered_by_director TEXT DEFAULT ''")
        if "is_golden_mask" not in prod_cols:
            conn.execute("ALTER TABLE productions ADD COLUMN is_golden_mask INTEGER NOT NULL DEFAULT 0")

        comp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(companions)").fetchall()}
        if "birth_date" not in comp_cols:
            conn.execute("ALTER TABLE companions ADD COLUMN birth_date TEXT DEFAULT ''")

        # Настройки по умолчанию
        defaults = {
            "max_price_per_ticket": "60000",
            "seat_rule": "1) Ряд сразу после прохода · 2) Середина рядов 1–8 партера · 3) Середина бельэтажа / балкона 1 этажа",
            "autonomy_mode": "hybrid_sniper",
            "payment_method": "card_3ds",
            "card_masked": "",
            "card_encrypted": "",
            "daily_scan_time": "09:30",
            "last_scan_at": "",
            "last_scan_summary": "Готов к проверке официальных сайтов",
            "tg_bot_token_encrypted": "",
            "tg_bot_username": "",
            "tg_bot_status": "Ожидает токен от @BotFather",
            "tg_last_update_id": "0",
            "mail_address": "Lesteatres@mail.ru",
            "mail_imap_host": "imap.mail.ru",
            "mail_password_encrypted": "",
            "mail_status": "Ожидает пароль приложения Mail.ru",
            "mail_last_check_at": "",
            "mail_imported_count": "0",
        }
        for k, v in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (k, v),
            )

        # Зрители: строго два варианта везде — А и +1 (по умолчанию 2 билета рядом: А и +1)
        cur = conn.execute("SELECT COUNT(*) AS cnt FROM companions").fetchone()
        if cur["cnt"] == 0:
            conn.execute(
                """
                INSERT INTO companions (name, role, full_name, is_self, doc_masked)
                VALUES
                    ('А', 'self', 'Основной пользователь', 1, 'Не заполнен (для именных касс)'),
                    ('+1', 'companion', '', 0, 'Не заполнен (для именных касс)')
                """
            )
        else:
            # Миграция переименования К / А+1 -> +1 и удаление всех лишних вариантов кроме А и +1
            for old_name in ("К", "А+1"):
                conn.execute("UPDATE companions SET name = '+1' WHERE name = ?", (old_name,))
                conn.execute("UPDATE away_periods SET person_name = '+1' WHERE person_name = ?", (old_name,))
                conn.execute("UPDATE OR IGNORE person_decisions SET person_name = '+1' WHERE person_name = ?", (old_name,))
                conn.execute("DELETE FROM person_decisions WHERE person_name = ?", (old_name,))
                conn.execute("UPDATE OR IGNORE person_rules SET person_name = '+1' WHERE person_name = ?", (old_name,))
                conn.execute("DELETE FROM person_rules WHERE person_name = ?", (old_name,))
                conn.execute("UPDATE OR IGNORE person_taste_weights SET person_name = '+1' WHERE person_name = ?", (old_name,))
                conn.execute("DELETE FROM person_taste_weights WHERE person_name = ?", (old_name,))
            conn.execute("DELETE FROM companions WHERE name NOT IN ('А', '+1')")
            conn.execute("DELETE FROM away_periods WHERE person_name NOT IN ('А', '+1')")
            conn.execute(
                "UPDATE wallet_tickets SET who_goes = 'А + +1' WHERE who_goes IN ('А + К', 'А + А+1', 'А + +1 + Щ')"
            )

        # Обязательные официальные источники стартового периметра
        initial_sources = [
            ("theatre_site", "https://vakhtangov.ru", "Театр им. Евг. Вахтангова (офиц. касса)"),
            ("theatre_site", "https://mxat.ru", "МХТ (офиц. касса mxat.ru)"),
            ("theatre_site", "https://www.theatreofnations.ru", "Театр Наций (офиц. касса)"),
            ("theatre_site", "https://www.ramt.ru", "РАМТ (офиц. касса)"),
            ("theatre_site", "https://teatrpushkin.ru", "Театр им. А. С. Пушкина (офиц. касса)"),
            ("theatre_site", "https://www.satirikon.ru", "Театр «Сатирикон» (офиц. касса)"),
            ("theatre_site", "https://o3ero.ru", "Театр «Озеро» на Рождественке (офиц. сайт)"),
            ("theatre_site", "https://bolshoi.ru", "Большой театр России (офиц. касса bolshoi.ru)"),
            ("theatre_site", "https://stanmus.ru", "МАМТ им. Станиславского и Немировича-Данченко (stanmus.ru)"),
            ("theatre_site", "https://www.okolo.ru", "Театр «Около дома Станиславского» (okolo.ru)"),
            ("theatre_site", "https://fomenki.ru", "Мастерская Петра Фоменко (fomenki.ru)"),
            ("theatre_site", "https://sti.ru", "Студия театрального искусства — СТИ (sti.ru)"),
            ("theatre_site", "https://teatrcdr.ru", "Центр драматургии и режиссуры — ЦДР (teatrcdr.ru)"),
            ("theatre_site", "https://tabakov.ru", "Театр Олега Табакова (tabakov.ru)"),
            ("theatre_site", "https://mikhalkov12.ru", "Мастерская «12» (mikhalkov12.ru)"),
            ("telegram", "@o3epotheatre", "Telegram Театра «Озеро» (дропы билетов)"),
            ("telegram", "@bolshoi_theatre", "Telegram Большого театра (график стартов продаж)"),
            ("telegram", "@stanmus", "Telegram МАМТ (премьеры и старты продаж)"),
            ("telegram", "@vakhtangov_theatre", "Telegram Театра Вахтангова (анонсы стартов продаж)"),
            ("telegram", "@mxat_chekhova", "Telegram МХТ (анонсы стартов продаж)"),
            ("telegram", "@theatreofnations", "Telegram Театра Наций (старты продаж)"),
        ]
        for kind, url, title in initial_sources:
            conn.execute(
                "INSERT OR IGNORE INTO sources (kind, handle_or_url, title) VALUES (?, ?, ?)",
                (kind, url, title),
            )
        conn.execute("UPDATE sources SET title = 'МХТ (офиц. касса mxat.ru)' WHERE handle_or_url = 'https://mxat.ru'")
        conn.execute("UPDATE sources SET title = 'Telegram МХТ (анонсы стартов продаж)' WHERE handle_or_url = '@mxat_chekhova'")

        # Эталонный каталог постановок
        now = datetime.now().isoformat(timespec="seconds")
        for prod in SEED_PRODUCTIONS:
            venue = OFFICIAL_VENUES[prod["venue_id"]]
            hall = HALL_GEOMETRY[prod["hall_id"]]
            top_target = hall["targets"][0]
            default_offer = f"{top_target['zone']}, ряд {top_target['row']}, места {top_target['seats']}"

            stage_dir = prod.get("stage_director") or prod["director"]
            is_gm = 1 if (prod.get("golden_mask") or prod["slug"] in GOLDEN_MASK_LAUREATE_SLUGS) else 0
            conn.execute(
                """
                INSERT OR IGNORE INTO productions (
                    slug, title, director, stage_director, category, venue_id, venue_name, hall_id,
                    official_url, duration, director_hidden_on_site, is_high_demand, is_golden_mask,
                    note, status, sniper_enabled, default_party, source_type, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'catalog', ?)
                """,
                (
                    prod["slug"],
                    prod["title"],
                    prod["director"],
                    stage_dir,
                    prod["category"],
                    prod["venue_id"],
                    venue["name"],
                    prod["hall_id"],
                    prod["official_url"],
                    prod["duration"],
                    1 if prod["director_hidden_on_site"] else 0,
                    1 if prod["is_high_demand"] else 0,
                    is_gm,
                    prod["note"],
                    "inbox",
                    0,
                    json.dumps([1, 2]),
                    now,
                ),
            )

            conn.execute(
                "UPDATE productions SET category = ?, stage_director = ?, venue_name = ?, is_golden_mask = ?, note = ? WHERE slug = ?",
                (prod["category"], stage_dir, venue["name"], is_gm, prod["note"], prod["slug"]),
            )

            for p_name in ("А", "+1"):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO person_decisions (person_name, production_slug, status, updated_at)
                    VALUES (?, ?, 'inbox', ?)
                    """,
                    (p_name, prod["slug"], now),
                )

            for ev in prod["fallback_events"]:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        production_slug, event_date, event_time, price_min, price_max,
                        seats_hint, target_seat_offer, is_live_verified, ticket_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        prod["slug"],
                        ev["date"],
                        ev["time"],
                        ev["price_min"],
                        ev["price_max"],
                        ev["seats_hint"],
                        default_offer,
                        prod["official_url"],
                    ),
                )

        for vid, vinfo in OFFICIAL_VENUES.items():
            conn.execute("UPDATE productions SET venue_name = ? WHERE venue_id = ?", (vinfo["name"], vid))

        # Персональное правило по умолчанию для А: не показывать оперу в Большом театре
        seeded_a = conn.execute(
            "SELECT value FROM settings WHERE key = 'seeded_rules_a_v1'"
        ).fetchone()
        if not seeded_a:
            conn.execute(
                """
                INSERT OR IGNORE INTO person_rules (person_name, rule_kind, rule_value, label, created_at)
                VALUES ('А', 'exclude_bolshoi_opera', 'bolshoi_opera', 'Не показывать оперу в Большом театре', ?)
                """,
                (now,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('seeded_rules_a_v1', '1')"
            )

        seeded_aplus1_away = conn.execute(
            "SELECT value FROM settings WHERE key = 'seeded_away_aplus1_v1'"
        ).fetchone()
        if not seeded_aplus1_away:
            existing_aw = conn.execute(
                "SELECT id FROM away_periods WHERE person_name = '+1' AND start_date = '2026-12-28' AND end_date = '2027-01-10'"
            ).fetchone()
            if not existing_aw:
                conn.execute(
                    """
                    INSERT INTO away_periods (person_name, is_primary, start_date, end_date, note, created_at)
                    VALUES ('+1', 0, '2026-12-28', '2027-01-10', 'Нет в Москве', ?)
                    """,
                    (now,),
                )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('seeded_away_aplus1_v1', '1')"
            )

        # Пометка по просьбе пользователя: на «Грозе» в Театре «Озеро» были оба (А и +1), спектакль в «Нравятся»
        seeded_groza = conn.execute(
            "SELECT value FROM settings WHERE key = 'seeded_groza_both_seen_v1'"
        ).fetchone()
        if not seeded_groza:
            conn.execute(
                "UPDATE productions SET status = 'seen', is_liked = 1, updated_at = ? WHERE slug = 'ozero-groza'",
                (now,),
            )
            for p_name in ("А", "+1"):
                conn.execute(
                    """
                    INSERT INTO person_decisions (person_name, production_slug, status, updated_at)
                    VALUES (?, 'ozero-groza', 'seen', ?)
                    ON CONFLICT(person_name, production_slug) DO UPDATE SET
                        status = 'seen',
                        updated_at = excluded.updated_at
                    """,
                    (p_name, now),
                )
            comp_rows = conn.execute(
                "SELECT id FROM companions WHERE name IN ('А', '+1')"
            ).fetchall()
            for cr in comp_rows:
                conn.execute(
                    "INSERT OR IGNORE INTO companion_seen (companion_id, production_slug) VALUES (?, 'ozero-groza')",
                    (cr["id"],),
                )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('seeded_groza_both_seen_v1', '1')"
            )

        seeded_person_likes_groza = conn.execute(
            "SELECT value FROM settings WHERE key = 'seeded_person_likes_groza_v1'"
        ).fetchone()
        if not seeded_person_likes_groza:
            for p_name in ("А", "+1"):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO person_likes (person_name, production_slug, created_at)
                    VALUES (?, 'ozero-groza', ?)
                    """,
                    (p_name, now),
                )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('seeded_person_likes_groza_v1', '1')"
            )

        # Правка по указанию пользователя:
        # - «Грозу» видели оба, но она НЕ в лайках (убрать из Нравятся и убрать её автопоиск)
        # - «Клоуны» (Александра Толстошева) — видели и лайк
        # - Крымов, Туминас, Бутусов, Александра Толстошева — лайк / отслеживаем
        fixed_likes_v1 = conn.execute(
            "SELECT value FROM settings WHERE key = 'seeded_klouny_and_creators_v1'"
        ).fetchone()
        if not fixed_likes_v1:
            conn.execute("DELETE FROM person_likes WHERE production_slug = 'ozero-groza'")
            conn.execute("UPDATE productions SET is_liked = 0, updated_at = ? WHERE slug = 'ozero-groza'", (now,))
            conn.execute(
                "DELETE FROM productions WHERE slug IN ('nations-isadora-smolnikova', 'taganka-tri-porosenka-tregubova')"
            )

            conn.execute(
                "UPDATE productions SET status = 'seen', is_liked = 1, sniper_enabled = 0, updated_at = ? WHERE slug = 'okolo-klouny'",
                (now,),
            )
            for p_name in ("А", "+1"):
                conn.execute(
                    """
                    INSERT INTO person_decisions (person_name, production_slug, status, updated_at)
                    VALUES (?, 'okolo-klouny', 'seen', ?)
                    ON CONFLICT(person_name, production_slug) DO UPDATE SET
                        status = 'seen',
                        updated_at = excluded.updated_at
                    """,
                    (p_name, now),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO person_likes (person_name, production_slug, created_at)
                    VALUES (?, 'okolo-klouny', ?)
                    """,
                    (p_name, now),
                )
                for cr_name, cr_role, cr_note in (
                    ("Дмитрий Крымов", "Режиссёр", "Отслеживаем"),
                    ("Римас Туминас", "Режиссёр", "Отслеживаем"),
                    ("Юрий Бутусов", "Режиссёр", "Отслеживаем"),
                    ("Александра Толстошева", "Режиссёр", "По лайку «Клоуны»"),
                ):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO tracked_creators (person_name, name, role, source_note, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (p_name, cr_name, cr_role, cr_note, now),
                    )
            comp_rows = conn.execute("SELECT id FROM companions WHERE name IN ('А', '+1')").fetchall()
            for cr in comp_rows:
                conn.execute(
                    "INSERT OR IGNORE INTO companion_seen (companion_id, production_slug) VALUES (?, 'okolo-klouny')",
                    (cr["id"],),
                )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('seeded_klouny_and_creators_v1', '1')"
            )


def get_settings_dict() -> dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def list_companions() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, role, full_name, birth_date, doc_type, doc_masked, phone, email, is_self
            FROM companions
            WHERE active=1 AND name IN ('А', '+1')
            ORDER BY CASE WHEN name = 'А' THEN 0 ELSE 1 END ASC
            """
        ).fetchall()
        seen_rows = conn.execute("SELECT companion_id, production_slug FROM companion_seen").fetchall()

    seen_map: dict[int, list[str]] = {}
    for s in seen_rows:
        seen_map.setdefault(s["companion_id"], []).append(s["production_slug"])

    result = []
    for r in rows:
        d = dict(r)
        d["seen_slugs"] = seen_map.get(d["id"], [])
        result.append(d)
    return result


def list_away_periods() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM away_periods
            ORDER BY
                CASE WHEN person_name = 'А' THEN 0 WHEN person_name = '+1' THEN 1 ELSE 2 END ASC,
                start_date ASC,
                id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def add_away_period(
    start_date: str,
    end_date: str,
    person_name: str = "А",
    is_primary: bool = True,
    note: str = "Нет в Москве",
) -> dict[str, Any]:
    s_dt = start_date.strip()
    e_dt = (end_date or start_date).strip()
    if e_dt < s_dt:
        s_dt, e_dt = e_dt, s_dt
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO away_periods (person_name, is_primary, start_date, end_date, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (person_name.strip() or "А", 1 if is_primary else 0, s_dt, e_dt, note.strip() or "Нет в Москве", now),
        )
        row = conn.execute("SELECT * FROM away_periods WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def delete_away_period(period_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM away_periods WHERE id = ?", (period_id,))


GENRE_LABELS: dict[str, str] = {
    "theatre": "Драматический театр",
    "ballet": "Балет",
    "opera": "Опера",
    "special": "Спецпоказ / Кинотеатр",
}


def production_matches_rule(prod: dict[str, Any], rule: dict[str, Any]) -> bool:
    kind = rule["rule_kind"]
    val = rule["rule_value"]
    cat = (prod.get("category") or "").lower()
    venue_id = (prod.get("venue_id") or "").lower()
    title_low = (prod.get("title") or "").lower()
    director = prod.get("director") or ""

    if kind == "exclude_bolshoi_opera":
        return ("bolshoi" in venue_id) and (cat == "opera" or "опера" in title_low)
    if kind == "exclude_bolshoi_ballet":
        return ("bolshoi" in venue_id) and (cat == "ballet" or "балет" in title_low)
    if kind == "exclude_category":
        return cat == val.lower()
    if kind == "exclude_director":
        return director.lower() == val.lower()
    if kind == "exclude_venue":
        return venue_id == val.lower()
    return False


def list_person_rules(person_name: str | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if person_name:
            rows = conn.execute(
                "SELECT * FROM person_rules WHERE person_name = ? ORDER BY id ASC",
                (person_name,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM person_rules ORDER BY person_name ASC, id ASC").fetchall()
    return [dict(r) for r in rows]


def add_person_rule(person_name: str, rule_kind: str, rule_value: str, label: str) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO person_rules (person_name, rule_kind, rule_value, label, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (person_name.strip(), rule_kind.strip(), rule_value.strip(), label.strip(), now),
        )
        row = conn.execute(
            "SELECT * FROM person_rules WHERE person_name = ? AND rule_kind = ? AND rule_value = ?",
            (person_name.strip(), rule_kind.strip(), rule_value.strip()),
        ).fetchone()
    return dict(row)


def delete_person_rule(rule_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM person_rules WHERE id = ?", (rule_id,))


def _extract_prod_features(prod: dict[str, Any]) -> list[tuple[str, str, float]]:
    """Возвращает (feature_key, human_label, multiplier) для онлайн-обучения вкуса."""
    cat = (prod.get("category") or "theatre").lower()
    venue_id = (prod.get("venue_id") or "").lower()
    director = prod.get("director") or ""
    stage_director = prod.get("stage_director") or ""

    feats: list[tuple[str, str, float]] = []
    if "bolshoi" in venue_id and cat == "opera":
        feats.append(("subgenre:bolshoi_opera", "Опера в Большом", 1.2))
    elif "bolshoi" in venue_id and cat == "ballet":
        feats.append(("subgenre:bolshoi_ballet", "Балет в Большом", 1.2))
    elif "mamt" in venue_id and cat == "opera":
        feats.append(("subgenre:mamt_opera", "Опера в МАМТ", 1.15))
    elif "mamt" in venue_id and cat == "ballet":
        feats.append(("subgenre:mamt_ballet", "Балет в МАМТ", 1.15))
    else:
        feats.append((f"category:{cat}", GENRE_LABELS.get(cat, cat), 1.0))

    for creator in split_director_names(stage_director or director):
        if creator not in ("Большой театр", "МАМТ", "Театр «Озеро»", "Театр «Около»"):
            feats.append((f"director:{creator}", creator, 1.35))
    if venue_id and "bolshoi" not in venue_id:
        v_short = OFFICIAL_VENUES.get(venue_id, {}).get("short_name") or prod.get("venue_name") or venue_id
        feats.append((f"venue:{venue_id}", f"Сцена: {v_short}", 0.7))
    return feats


def split_director_names(raw: str) -> list[str]:
    """Разбивает строку режиссёров/хореографов на отдельные имена для поиска по театрам Москвы."""
    if not raw:
        return []
    import re as _re

    cleaned = _re.sub(r"\([^)]*\)", "", raw)
    parts = _re.split(r"[,;/]", cleaned)
    ignore = {"Большой театр", "МАМТ", "Театр «Озеро»", "Театр «Около»", "Неизвестно"}
    names: list[str] = []
    for part in parts:
        nm = part.strip()
        if len(nm) >= 4 and nm not in ignore and nm not in names:
            names.append(nm)
    return names


def recompute_person_taste(person_name: str) -> None:
    """Пересчитывает веса предпочтений конкретного человека (А или +1) по его свайпам и оценкам после спектаклей."""
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT pd.status, p.slug, p.title, p.director, p.stage_director, p.category, p.venue_id, p.venue_name
            FROM person_decisions pd
            JOIN productions p ON p.slug = pd.production_slug
            WHERE pd.person_name = ? AND pd.status != 'inbox'
            """,
            (person_name,),
        ).fetchall()
        liked_slugs = {
            r["production_slug"]
            for r in conn.execute(
                "SELECT production_slug FROM person_likes WHERE person_name = ?",
                (person_name,),
            ).fetchall()
        }
        disliked_slugs = {
            r["production_slug"]
            for r in conn.execute(
                "SELECT production_slug FROM post_show_reviews WHERE person_name = ? AND verdict = 'disliked' AND production_slug != ''",
                (person_name,),
            ).fetchall()
        }

        agg: dict[str, dict[str, Any]] = {}
        for r in rows:
            st = r["status"]
            slug = r["slug"]
            prod = dict(r)
            for f_key, f_lbl, mult in _extract_prod_features(prod):
                entry = agg.setdefault(
                    f_key,
                    {"label": f_lbl, "score": 0.0, "likes": 0, "skips": 0, "seen_cnt": 0},
                )
                if st in ("want", "sniper"):
                    entry["score"] += 1.5 * mult
                    entry["likes"] += 1
                elif st == "seen":
                    entry["seen_cnt"] += 1
                    if slug in liked_slugs:
                        entry["score"] += 2.0 * mult
                        entry["likes"] += 1
                    elif slug in disliked_slugs:
                        entry["score"] -= 1.5 * mult
                        entry["skips"] += 1
                    else:
                        entry["score"] += 0.35 * mult
                elif st == "skip":
                    entry["score"] -= 1.25 * mult
                    entry["skips"] += 1

        conn.execute("DELETE FROM person_taste_weights WHERE person_name = ?", (person_name,))
        for f_key, data in agg.items():
            conn.execute(
                """
                INSERT INTO person_taste_weights (
                    person_name, feature_key, feature_label, score, likes, skips, seen_cnt, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    person_name,
                    f_key,
                    data["label"],
                    round(data["score"], 2),
                    data["likes"],
                    data["skips"],
                    data["seen_cnt"],
                    now,
                ),
            )


def sync_liked_directors_discovery() -> list[dict[str, Any]]:
    """
    Собирает режиссёров и хореографов понравившихся спектаклей (person_likes, Хочу/Охота и manual_liked_plays)
    и автоматически добавляет их постановки из других театров Москвы в «Отбор» (1-ю вкладку).
    """
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        liked_rows = conn.execute(
            """
            SELECT DISTINCT p.slug, p.title, p.director, p.stage_director, p.venue_id, p.category
            FROM productions p
            LEFT JOIN person_decisions pd ON pd.production_slug = p.slug
            LEFT JOIN person_likes pl ON pl.production_slug = p.slug
            WHERE pl.production_slug IS NOT NULL OR p.is_liked = 1 OR pd.status IN ('want', 'sniper')
            """
        ).fetchall()
        manual_rows = conn.execute("SELECT * FROM manual_liked_plays").fetchall()
        creator_rows = conn.execute("SELECT DISTINCT name FROM tracked_creators").fetchall()

        tracked_names: set[str] = set()
        for r in liked_rows:
            for nm in split_director_names(r["stage_director"] or r["director"]):
                tracked_names.add(nm)
        for mr in manual_rows:
            for nm in split_director_names(mr["stage_director"]):
                tracked_names.add(nm)
        for cr in creator_rows:
            for nm in split_director_names(cr["name"]):
                tracked_names.add(nm)
            if cr["name"].strip():
                tracked_names.add(cr["name"].strip())

        added: list[dict[str, Any]] = []
        for cand in DIRECTOR_MOSCOW_DISCOVERY:
            cand_dirs = cand.get("matched_directors") or split_director_names(cand.get("stage_director", ""))
            matched = [d for d in cand_dirs if d in tracked_names]
            if not matched:
                continue

            matched_str = ", ".join(matched)
            venue = OFFICIAL_VENUES[cand["venue_id"]]
            hall = HALL_GEOMETRY[cand["hall_id"]]
            top_target = hall["targets"][0]
            default_offer = f"{top_target['zone']}, ряд {top_target['row']}, места {top_target['seats']}"

            existing = conn.execute("SELECT slug FROM productions WHERE slug = ?", (cand["slug"],)).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO productions (
                        slug, title, director, stage_director, category, venue_id, venue_name, hall_id,
                        official_url, duration, director_hidden_on_site, is_high_demand,
                        note, status, sniper_enabled, default_party, source_type, discovered_by_director, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'inbox', 0, '[1,2]', 'director_radar', ?, ?)
                    """,
                    (
                        cand["slug"],
                        cand["title"],
                        cand["director"],
                        cand["stage_director"],
                        cand["category"],
                        cand["venue_id"],
                        venue["name"],
                        cand["hall_id"],
                        cand["official_url"],
                        cand["duration"],
                        1 if cand.get("is_high_demand") else 0,
                        cand["note"],
                        matched_str,
                        now,
                    ),
                )
                for p_name in ("А", "+1"):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO person_decisions (person_name, production_slug, status, updated_at)
                        VALUES (?, ?, 'inbox', ?)
                        """,
                        (p_name, cand["slug"], now),
                    )
                for ev in cand.get("fallback_events", []):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO events (
                            production_slug, event_date, event_time, price_min, price_max,
                            seats_hint, target_seat_offer, is_live_verified, ticket_url
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                        """,
                        (
                            cand["slug"],
                            ev["date"],
                            ev["time"],
                            ev["price_min"],
                            ev["price_max"],
                            ev["seats_hint"],
                            default_offer,
                            cand["official_url"],
                        ),
                    )
                added.append({"slug": cand["slug"], "title": cand["title"], "matched_director": matched_str})
            else:
                conn.execute(
                    "UPDATE productions SET discovered_by_director = ?, stage_director = ? WHERE slug = ?",
                    (matched_str, cand["stage_director"], cand["slug"]),
                )
    return added


def _auto_track_creators_from_play(
    conn: Any,
    person_name: str,
    stage_director: str,
    category: str,
    play_title: str,
    now: str,
) -> None:
    role = "Хореограф" if category == "ballet" else "Режиссёр"
    for nm in split_director_names(stage_director):
        conn.execute(
            """
            INSERT OR IGNORE INTO tracked_creators (person_name, name, role, source_note, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (person_name, nm, role, f"По лайку «{play_title}»" if play_title else "По лайку", now),
        )


def list_tracked_creators(person_name: str = "А") -> list[dict[str, Any]]:
    person = "+1" if (person_name or "А").strip() in ("К", "А+1", "+1") else "А"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tracked_creators WHERE person_name = ? ORDER BY id ASC",
            (person,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_tracked_creator(
    person_name: str,
    name: str,
    role: str = "Режиссёр",
    source_note: str = "Добавлено вручную",
) -> dict[str, Any]:
    person = "+1" if (person_name or "А").strip() in ("К", "А+1", "+1") else "А"
    clean_name = name.strip()
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tracked_creators (person_name, name, role, source_note, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(person_name, name) DO UPDATE SET
                role = excluded.role,
                source_note = excluded.source_note
            """,
            (person, clean_name, (role or "Режиссёр").strip(), source_note.strip(), now),
        )
        row = conn.execute(
            "SELECT * FROM tracked_creators WHERE person_name = ? AND name = ?",
            (person, clean_name),
        ).fetchone()
    sync_liked_directors_discovery()
    return dict(row)


def delete_tracked_creator(creator_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM tracked_creators WHERE id = ?", (creator_id,))


def toggle_production_like(slug: str, liked: bool, person_name: str = "А") -> None:
    now = datetime.now().isoformat(timespec="seconds")
    person = "+1" if (person_name or "А").strip() in ("К", "А+1", "+1") else "А"
    with get_conn() as conn:
        prod_row = conn.execute("SELECT * FROM productions WHERE slug = ?", (slug,)).fetchone()
        if liked:
            conn.execute(
                """
                INSERT OR IGNORE INTO person_likes (person_name, production_slug, created_at)
                VALUES (?, ?, ?)
                """,
                (person, slug, now),
            )
            conn.execute(
                "UPDATE productions SET is_liked = 1, updated_at = ? WHERE slug = ?",
                (now, slug),
            )
            if prod_row:
                _auto_track_creators_from_play(
                    conn,
                    person,
                    prod_row["stage_director"] or prod_row["director"],
                    prod_row["category"],
                    prod_row["title"],
                    now,
                )
        else:
            conn.execute(
                "DELETE FROM person_likes WHERE person_name = ? AND production_slug = ?",
                (person, slug),
            )
            # Если карточка была в «Нравятся» из-за свайпа вправо («Хочу»), снимаем этот статус у пользователя
            cur_dec = conn.execute(
                "SELECT status FROM person_decisions WHERE person_name = ? AND production_slug = ?",
                (person, slug),
            ).fetchone()
            if cur_dec and cur_dec["status"] in ("want", "sniper"):
                conn.execute(
                    "UPDATE person_decisions SET status = 'inbox', updated_at = ? WHERE person_name = ? AND production_slug = ?",
                    (now, person, slug),
                )
            any_want = conn.execute(
                "SELECT 1 FROM person_decisions WHERE production_slug = ? AND status IN ('want', 'sniper') LIMIT 1",
                (slug,),
            ).fetchone()
            any_like = conn.execute(
                "SELECT 1 FROM person_likes WHERE production_slug = ? LIMIT 1",
                (slug,),
            ).fetchone()
            conn.execute(
                "UPDATE productions SET sniper_enabled = ?, is_liked = ?, updated_at = ? WHERE slug = ?",
                (1 if any_want else 0, 1 if any_like else 0, now, slug),
            )
    recompute_person_taste(person)
    if liked:
        sync_liked_directors_discovery()


def add_manual_liked_play(
    title: str,
    stage_director: str = "",
    venue_name: str = "",
    category: str = "theatre",
    note: str = "",
    person_name: str = "А",
) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    person = "+1" if (person_name or "А").strip() in ("К", "А+1", "+1") else "А"
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO manual_liked_plays (title, stage_director, venue_name, category, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title.strip(), (stage_director or "").strip(), venue_name.strip(), category.strip() or "theatre", note.strip(), now),
        )
        if stage_director and stage_director.strip():
            _auto_track_creators_from_play(conn, person, stage_director.strip(), category or "theatre", title.strip(), now)
        row = conn.execute("SELECT * FROM manual_liked_plays WHERE id = ?", (cur.lastrowid,)).fetchone()
    if stage_director and stage_director.strip():
        sync_liked_directors_discovery()
    return dict(row)


def delete_manual_liked_play(item_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM manual_liked_plays WHERE id = ?", (item_id,))


def set_person_decision(person_name: str, slug: str, status: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO person_decisions (person_name, production_slug, status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(person_name, production_slug) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (person_name, slug, status, now),
        )
        if status == "skip":
            conn.execute(
                "DELETE FROM person_likes WHERE person_name = ? AND production_slug = ?",
                (person_name, slug),
            )
        any_want = conn.execute(
            """
            SELECT 1 FROM person_decisions
            WHERE production_slug = ? AND status IN ('want', 'sniper')
            LIMIT 1
            """,
            (slug,),
        ).fetchone()
        any_like = conn.execute(
            "SELECT 1 FROM person_likes WHERE production_slug = ? LIMIT 1",
            (slug,),
        ).fetchone()
        conn.execute(
            "UPDATE productions SET sniper_enabled = ?, is_liked = ?, updated_at = ? WHERE slug = ?",
            (1 if any_want else 0, 1 if any_like else 0, now, slug),
        )
        if status in ("want", "sniper"):
            prod_row = conn.execute("SELECT * FROM productions WHERE slug = ?", (slug,)).fetchone()
            if prod_row:
                _auto_track_creators_from_play(
                    conn,
                    person_name,
                    prod_row["stage_director"] or prod_row["director"],
                    prod_row["category"],
                    prod_row["title"],
                    now,
                )
        if status == "seen":
            comp_row = conn.execute("SELECT id FROM companions WHERE name = ?", (person_name,)).fetchone()
            if comp_row:
                conn.execute(
                    "INSERT OR IGNORE INTO companion_seen (companion_id, production_slug) VALUES (?, ?)",
                    (comp_row["id"], slug),
                )
    recompute_person_taste(person_name)
    if status in ("want", "sniper"):
        sync_liked_directors_discovery()


def _ticket_includes_person(who_goes: str, person_name: str) -> bool:
    raw = (who_goes or "").strip()
    if not raw:
        return True
    if person_name == "+1":
        return ("+1" in raw) or ("А+1" in raw)
    cleaned = raw.replace("А+1", "").replace("+1", "")
    return ("А" in cleaned) or ("Я" in cleaned)


def _norm_play_title(title: str) -> str:
    import re as _re

    t = (title or "").lower().strip()
    t = _re.sub(r"[«»\"'„“”]", "", t)
    t = _re.sub(r"\s+", " ", t)
    return t


def _parse_hhmm_mins(hhmm: str) -> int:
    try:
        parts = (hhmm or "19:00").strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return 19 * 60


def list_post_show_state(person_name: str = "А") -> dict[str, Any]:
    """
    Находит прошедшие спектакли (из wallet_tickets), по которым приложение ещё не спросило
    «Понравилось или нет?», а также возвращает карту уже выставленных оценок.
    """
    person = "+1" if (person_name or "А").strip() in ("К", "А+1", "+1") else "А"
    now = datetime.now()
    today_iso = now.date().isoformat()
    now_mins = now.hour * 60 + now.minute

    with get_conn() as conn:
        tickets = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM wallet_tickets ORDER BY event_date DESC, event_time DESC, id DESC"
            ).fetchall()
        ]
        reviews = conn.execute(
            "SELECT item_key, verdict FROM post_show_reviews WHERE person_name = ?",
            (person,),
        ).fetchall()
        prods = [dict(r) for r in conn.execute("SELECT * FROM productions").fetchall()]

    reviewed_map: dict[str, str] = {r["item_key"]: r["verdict"] for r in reviews}
    prod_by_norm_title: dict[str, dict[str, Any]] = {
        _norm_play_title(p["title"]): p for p in prods
    }

    pending: list[dict[str, Any]] = []
    seen_dedupe: set[str] = set()

    for t in tickets:
        ev_date = (t.get("event_date") or "").strip()
        if not ev_date:
            continue
        start_mins = _parse_hhmm_mins(t.get("event_time") or "19:00")
        is_past = (ev_date < today_iso) or (ev_date == today_iso and now_mins >= start_mins + 180)
        if not is_past:
            continue
        if not _ticket_includes_person(t.get("who_goes", ""), person):
            continue

        item_key = f"ticket:{t['id']}"
        if item_key in reviewed_map:
            continue

        norm_t = _norm_play_title(t["title"])
        if not norm_t or norm_t in seen_dedupe:
            continue
        seen_dedupe.add(norm_t)

        matched_prod = prod_by_norm_title.get(norm_t)
        pending.append(
            {
                "item_key": item_key,
                "ticket_id": t["id"],
                "title": matched_prod["title"] if matched_prod else t["title"],
                "venue_name": (matched_prod["venue_name"] if matched_prod else t.get("venue_name")) or "",
                "stage_director": (
                    (matched_prod.get("stage_director") or matched_prod["director"]) if matched_prod else ""
                ),
                "production_slug": matched_prod["slug"] if matched_prod else "",
                "event_date": ev_date,
                "event_time": t.get("event_time") or "19:00",
            }
        )

    return {
        "pending": pending,
        "reviewed": reviewed_map,
    }


def submit_post_show_review(
    item_key: str,
    person_name: str,
    verdict: str,
    title: str,
    venue_name: str = "",
    stage_director: str = "",
    production_slug: str = "",
    event_date: str = "",
) -> dict[str, Any]:
    """
    Сохраняет ответ после спектакля:
    - verdict == 'liked'    -> отмечает 'seen' и добавляет спектакль во вкладку «Нравятся»
    - verdict == 'disliked' -> отмечает 'seen' и не добавляет (убирает) из «Нравятся»
    - verdict == 'dismissed'-> просто скрывает опросник
    """
    if verdict not in ("liked", "disliked", "dismissed"):
        raise ValueError("Некорректная оценка")
    person = "+1" if (person_name or "А").strip() in ("К", "А+1", "+1") else "А"
    now = datetime.now().isoformat(timespec="seconds")
    clean_title = (title or "").strip()
    norm_t = _norm_play_title(clean_title)

    with get_conn() as conn:
        matched_prod = None
        if production_slug:
            matched_prod = conn.execute(
                "SELECT * FROM productions WHERE slug = ?", (production_slug,)
            ).fetchone()
        if not matched_prod and norm_t:
            for row in conn.execute("SELECT * FROM productions").fetchall():
                if _norm_play_title(row["title"]) == norm_t:
                    matched_prod = row
                    break

        resolved_slug = matched_prod["slug"] if matched_prod else ""
        resolved_venue = (matched_prod["venue_name"] if matched_prod else venue_name) or ""
        resolved_dir = (
            (matched_prod["stage_director"] or matched_prod["director"])
            if matched_prod
            else stage_director
        ) or ""

        conn.execute(
            """
            INSERT INTO post_show_reviews (
                item_key, person_name, production_slug, title, venue_name, stage_director, event_date, verdict, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_key, person_name) DO UPDATE SET
                verdict = excluded.verdict,
                production_slug = excluded.production_slug,
                stage_director = excluded.stage_director,
                created_at = excluded.created_at
            """,
            (
                item_key.strip(),
                person,
                resolved_slug,
                matched_prod["title"] if matched_prod else clean_title,
                resolved_venue,
                resolved_dir,
                event_date,
                verdict,
                now,
            ),
        )

        if verdict in ("liked", "disliked") and resolved_slug:
            conn.execute(
                """
                INSERT INTO person_decisions (person_name, production_slug, status, updated_at)
                VALUES (?, ?, 'seen', ?)
                ON CONFLICT(person_name, production_slug) DO UPDATE SET
                    status = 'seen',
                    updated_at = excluded.updated_at
                """,
                (person, resolved_slug, now),
            )
            comp_row = conn.execute("SELECT id FROM companions WHERE name = ?", (person,)).fetchone()
            if comp_row:
                conn.execute(
                    "INSERT OR IGNORE INTO companion_seen (companion_id, production_slug) VALUES (?, ?)",
                    (comp_row["id"], resolved_slug),
                )
            if verdict == "liked":
                conn.execute(
                    """
                    INSERT OR IGNORE INTO person_likes (person_name, production_slug, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (person, resolved_slug, now),
                )
                if matched_prod:
                    _auto_track_creators_from_play(
                        conn,
                        person,
                        resolved_dir,
                        matched_prod["category"],
                        matched_prod["title"],
                        now,
                    )
            else:
                conn.execute(
                    "DELETE FROM person_likes WHERE person_name = ? AND production_slug = ?",
                    (person, resolved_slug),
                )

            any_want = conn.execute(
                "SELECT 1 FROM person_decisions WHERE production_slug = ? AND status IN ('want', 'sniper') LIMIT 1",
                (resolved_slug,),
            ).fetchone()
            any_like = conn.execute(
                "SELECT 1 FROM person_likes WHERE production_slug = ? LIMIT 1",
                (resolved_slug,),
            ).fetchone()
            conn.execute(
                "UPDATE productions SET sniper_enabled = ?, is_liked = ?, updated_at = ? WHERE slug = ?",
                (1 if any_want else 0, 1 if any_like else 0, now, resolved_slug),
            )

        elif verdict == "liked" and not resolved_slug and clean_title:
            existing_manual = conn.execute(
                "SELECT id FROM manual_liked_plays WHERE lower(title) = lower(?)",
                (clean_title,),
            ).fetchone()
            if not existing_manual:
                conn.execute(
                    """
                    INSERT INTO manual_liked_plays (title, stage_director, venue_name, category, note, created_at)
                    VALUES (?, ?, ?, 'theatre', 'Понравилось после спектакля', ?)
                    """,
                    (clean_title, resolved_dir, resolved_venue, now),
                )
            if resolved_dir:
                _auto_track_creators_from_play(conn, person, resolved_dir, "theatre", clean_title, now)

    if verdict in ("liked", "disliked"):
        recompute_person_taste(person)
    if verdict == "liked":
        sync_liked_directors_discovery()
    return {"ok": True, "verdict": verdict, "slug": resolved_slug}


def score_production_for_person(prod: dict[str, Any], weights_map: dict[str, float]) -> float:
    total = 0.0
    for f_key, _, _ in _extract_prod_features(prod):
        total += weights_map.get(f_key, 0.0)
    return round(total, 2)


def get_person_taste_summary(person_name: str) -> dict[str, Any]:
    with get_conn() as conn:
        w_rows = conn.execute(
            "SELECT * FROM person_taste_weights WHERE person_name = ? ORDER BY score DESC",
            (person_name,),
        ).fetchall()
        dec_rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM person_decisions WHERE person_name = ? GROUP BY status",
            (person_name,),
        ).fetchall()
    rules = list_person_rules(person_name)
    counts = {r["status"]: r["cnt"] for r in dec_rows}
    swiped_total = sum(v for k, v in counts.items() if k != "inbox")

    favorites = [
        {"key": r["feature_key"], "label": r["feature_label"], "score": r["score"], "likes": r["likes"]}
        for r in w_rows
        if r["score"] >= 0.6
    ][:5]
    dislikes = [
        {"key": r["feature_key"], "label": r["feature_label"], "score": r["score"], "skips": r["skips"]}
        for r in sorted(w_rows, key=lambda x: x["score"])
        if r["score"] <= -0.8
    ][:5]

    return {
        "person_name": person_name,
        "swiped_total": swiped_total,
        "counts": counts,
        "favorites": favorites,
        "dislikes": dislikes,
        "rules": rules,
    }


