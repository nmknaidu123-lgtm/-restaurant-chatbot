"""
database.py - ONE place where all data is saved: the file restaurant.db (SQLite).
Menu, restaurant settings, staff, login tokens, discount codes, orders, bookings, events and expenses
all live here, so nothing is lost when the server restarts.
"""

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import json

from app.defaults import (DEFAULT_SETTINGS, DEFAULT_STAFF, DEFAULT_MENU,
                          DEFAULT_PACKAGES, NON_VEG_WORDS)

DB_PATH = Path(__file__).resolve().parent.parent / "restaurant.db"


@contextmanager
def db():
    """Open the database, give it to the caller, save and close afterwards."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # rows behave like dicts: row["price"]
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def guess_is_veg(dish: str, category: str) -> bool:
    text = f"{dish} {category}".lower()
    return not any(word in text for word in NON_VEG_WORDS)


def hash_password(password: str, salt: str) -> str:
    """Never store plain passwords - store a salted hash instead."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def new_password_record(password: str) -> tuple[str, str]:
    salt = secrets.token_hex(8)
    return salt, hash_password(password, salt)


def init_db() -> None:
    """Create the tables, and fill them with default data the first time."""
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS menu (
            dish      TEXT PRIMARY KEY,
            price     INTEGER NOT NULL,
            category  TEXT NOT NULL,
            available INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS staff (
            username      TEXT PRIMARY KEY,
            salt          TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token    TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            role     TEXT NOT NULL,
            expires  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS discounts (
            code       TEXT PRIMARY KEY,
            percent    INTEGER NOT NULL,
            uses_left  INTEGER NOT NULL,
            created_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS applied_discounts (
            session_id TEXT PRIMARY KEY,
            code       TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            session_id TEXT PRIMARY KEY,
            cart       TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'new',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bookings (
            session_id TEXT PRIMARY KEY,
            name       TEXT,
            people     INTEGER,
            date       TEXT,
            time       TEXT
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            category    TEXT NOT NULL,
            description TEXT,
            amount      INTEGER NOT NULL,
            created_by  TEXT
        );
        CREATE TABLE IF NOT EXISTS event_packages (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL UNIQUE,
            food_type      TEXT NOT NULL,          -- 'Veg' or 'Non-veg'
            price_per_head INTEGER NOT NULL DEFAULT 0,
            cost_per_head  INTEGER NOT NULL DEFAULT 0,
            items          TEXT NOT NULL,          -- JSON list of dish names
            description    TEXT
        );
        CREATE TABLE IF NOT EXISTS event_payments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id   INTEGER NOT NULL,
            date       TEXT NOT NULL,
            amount     INTEGER NOT NULL,
            method     TEXT NOT NULL,              -- Cash / UPI / Card / Bank transfer
            note       TEXT,
            created_by TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            date        TEXT NOT NULL,
            time        TEXT,
            description TEXT,
            guests      INTEGER NOT NULL DEFAULT 0
        );
        """)

        # Extra staff details (added later - safe to run on an existing database)
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(staff)")}
        for column in ("full_name", "position", "phone", "shift"):
            if column not in existing:
                conn.execute(f"ALTER TABLE staff ADD COLUMN {column} TEXT")
        if "daily_wage" not in existing:          # wage per day, for labour cost
            conn.execute("ALTER TABLE staff ADD COLUMN daily_wage INTEGER NOT NULL DEFAULT 0")

        # When each order was paid, so sales are counted on the right day (added later)
        order_cols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
        if "paid_at" not in order_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN paid_at TEXT")
        # Special requests: per-dish notes (spice level, for kids...) and one note for the whole order
        if "notes" not in order_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN notes TEXT")
        if "order_note" not in order_cols:
            conn.execute("ALTER TABLE orders ADD COLUMN order_note TEXT")

        # Phone number for bookings taken over the phone (added later - safe on old databases)
        booking_cols = {r["name"] for r in conn.execute("PRAGMA table_info(bookings)")}
        if "phone" not in booking_cols:
            conn.execute("ALTER TABLE bookings ADD COLUMN phone TEXT")

        # Number of plates / guests for each event (added later - safe on old databases)
        event_cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
        if "guests" not in event_cols:
            conn.execute("ALTER TABLE events ADD COLUMN guests INTEGER NOT NULL DEFAULT 0")
        # Money for each event (added later - safe on old databases)
        for column, kind in (("price_per_head", "INTEGER NOT NULL DEFAULT 0"),
                             ("cost_per_head", "INTEGER NOT NULL DEFAULT 0"),
                             ("extra_cost", "INTEGER NOT NULL DEFAULT 0"),
                             ("advance", "INTEGER NOT NULL DEFAULT 0"),
                             ("customer", "TEXT"), ("phone", "TEXT"),
                             ("menu", "TEXT")):              # JSON list of dish names served
            if column not in event_cols:
                conn.execute(f"ALTER TABLE events ADD COLUMN {column} {kind}")

        # Buffet events: veg and non-veg guest groups, status, extra charges (added later)
        event_cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
        moving_old_events = "veg_guests" not in event_cols
        for column, kind in (("event_type", "TEXT NOT NULL DEFAULT 'Other'"),
                             ("status", "TEXT NOT NULL DEFAULT 'Confirmed'"),
                             ("veg_guests", "INTEGER NOT NULL DEFAULT 0"),
                             ("nonveg_guests", "INTEGER NOT NULL DEFAULT 0"),
                             ("veg_package", "TEXT"), ("nonveg_package", "TEXT"),
                             ("veg_price", "INTEGER NOT NULL DEFAULT 0"),
                             ("veg_cost", "INTEGER NOT NULL DEFAULT 0"),
                             ("nonveg_price", "INTEGER NOT NULL DEFAULT 0"),
                             ("nonveg_cost", "INTEGER NOT NULL DEFAULT 0"),
                             ("veg_items", "TEXT"), ("nonveg_items", "TEXT"),
                             ("extra_charges", "INTEGER NOT NULL DEFAULT 0"),
                             ("is_public", "INTEGER NOT NULL DEFAULT 0")):   # 1 = customers can see it
            if column not in event_cols:
                conn.execute(f"ALTER TABLE events ADD COLUMN {column} {kind}")

        # Veg / non-veg tag for every dish (added later)
        menu_cols = {r["name"] for r in conn.execute("PRAGMA table_info(menu)")}
        if "is_veg" not in menu_cols:
            conn.execute("ALTER TABLE menu ADD COLUMN is_veg INTEGER")

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

        if conn.execute("SELECT COUNT(*) FROM menu").fetchone()[0] == 0:
            for category, dishes in DEFAULT_MENU.items():
                for dish, price in dishes.items():
                    conn.execute("INSERT INTO menu (dish, price, category) VALUES (?, ?, ?)",
                                 (dish, price, category))

        # Tag dishes that have no veg/non-veg tag yet, by their name and category
        for r in conn.execute("SELECT dish, category FROM menu WHERE is_veg IS NULL").fetchall():
            conn.execute("UPDATE menu SET is_veg = ? WHERE dish = ?",
                         (int(guess_is_veg(r["dish"], r["category"])), r["dish"]))

        # Starting buffet packages (only the first time, and only dishes that exist)
        if conn.execute("SELECT COUNT(*) FROM event_packages").fetchone()[0] == 0:
            dishes = {r["dish"] for r in conn.execute("SELECT dish FROM menu")}
            for pkg in DEFAULT_PACKAGES:
                items = [d for d in pkg["items"] if d in dishes]
                conn.execute("INSERT INTO event_packages (name, food_type, price_per_head, cost_per_head, "
                             "items, description) VALUES (?, ?, ?, ?, ?, ?)",
                             (pkg["name"], pkg["food_type"], pkg["price_per_head"], pkg["cost_per_head"],
                              json.dumps(items), pkg["description"]))

        # One time: move events made before buffet groups existed into the veg or non-veg group,
        # and move their single "advance" amount into the payments list
        if moving_old_events:
            veg_dish = {r["dish"]: bool(r["is_veg"]) for r in conn.execute("SELECT dish, is_veg FROM menu")}
            for e in conn.execute("SELECT * FROM events").fetchall():
                items = json.loads(e["menu"]) if e["menu"] else []
                group = "veg" if all(veg_dish.get(d, True) for d in items) else "nonveg"
                conn.execute(f"UPDATE events SET {group}_guests = ?, {group}_price = ?, {group}_cost = ?, "
                             f"{group}_items = ? WHERE id = ?",
                             (e["guests"] or 0, e["price_per_head"] or 0, e["cost_per_head"] or 0,
                              json.dumps(items), e["id"]))
                # events with no customer (like a music night) were public before - keep them public
                conn.execute("UPDATE events SET is_public = ? WHERE id = ?",
                             (0 if (e["customer"] or "").strip() else 1, e["id"]))
                if e["advance"]:
                    conn.execute("INSERT INTO event_payments (event_id, date, amount, method, note, created_by) "
                                 "VALUES (?, ?, ?, 'Cash', 'Advance', 'system')",
                                 (e["id"], e["date"], e["advance"]))
                    conn.execute("UPDATE events SET advance = 0 WHERE id = ?", (e["id"],))

        if conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 0:
            for username, password, role in DEFAULT_STAFF:
                salt, pw_hash = new_password_record(password)
                conn.execute(
                    "INSERT INTO staff (username, salt, password_hash, role, full_name, position, shift) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (username, salt, pw_hash, role, username.title(),
                     "Manager" if role == "admin" else "Waiter", "Full day"),
                )
