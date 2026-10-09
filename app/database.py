"""
database.py - ONE place where all data is saved: the file restaurant.db (SQLite).
Menu, restaurant settings, staff, login tokens, discount codes, orders and bookings
all live here, so nothing is lost when the server restarts.
"""

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.defaults import DEFAULT_SETTINGS, DEFAULT_STAFF, DEFAULT_MENU

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
        """)

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

        if conn.execute("SELECT COUNT(*) FROM menu").fetchone()[0] == 0:
            for category, dishes in DEFAULT_MENU.items():
                for dish, price in dishes.items():
                    conn.execute("INSERT INTO menu (dish, price, category) VALUES (?, ?, ?)",
                                 (dish, price, category))

        if conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 0:
            for username, password, role in DEFAULT_STAFF:
                salt, pw_hash = new_password_record(password)
                conn.execute("INSERT INTO staff VALUES (?, ?, ?, ?)", (username, salt, pw_hash, role))
