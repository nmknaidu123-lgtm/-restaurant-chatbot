"""
data.py - Easy helper functions to read and change data in the database.
"""

import json
from datetime import datetime

from app.database import db


# ---------------- Settings (restaurant name, info) ----------------
def get_setting(key: str) -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


# ---------------- Menu ----------------
def get_menu(include_unavailable: bool = False) -> dict[str, int]:
    """{dish: price}. By default only dishes that are available today."""
    sql = "SELECT dish, price FROM menu"
    if not include_unavailable:
        sql += " WHERE available = 1"
    with db() as conn:
        return {r["dish"]: r["price"] for r in conn.execute(sql + " ORDER BY category, dish")}


def get_menu_by_category() -> dict[str, dict[str, int]]:
    """{category: {dish: price}} - only available dishes (for customers)."""
    result: dict[str, dict[str, int]] = {}
    with db() as conn:
        for r in conn.execute("SELECT dish, price, category FROM menu WHERE available = 1 "
                              "ORDER BY category, dish"):
            result.setdefault(r["category"], {})[r["dish"]] = r["price"]
    return result


def get_menu_full() -> list[dict]:
    """Every dish with all details (for the admin page)."""
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM menu ORDER BY category, dish")]


# ---------------- Orders and bookings ----------------
def get_order_cart(session_id: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT cart FROM orders WHERE session_id = ?", (session_id,)).fetchone()
    return json.loads(row["cart"]) if row else {}


def save_order(session_id: str, cart: dict) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO orders (session_id, cart, status, updated_at) VALUES (?, ?, 'new', ?) "
            "ON CONFLICT(session_id) DO UPDATE SET cart = excluded.cart, updated_at = excluded.updated_at",
            (session_id, json.dumps(cart), datetime.now().isoformat(timespec="seconds")),
        )


def get_order_status(session_id: str) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT status FROM orders WHERE session_id = ?", (session_id,)).fetchone()
    return row["status"] if row else None


def save_booking(session_id: str, booking: dict) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO bookings (session_id, name, people, date, time) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET name = excluded.name, people = excluded.people, "
            "date = excluded.date, time = excluded.time",
            (session_id, booking.get("name"), booking.get("people"),
             booking.get("date"), booking.get("time")),
        )


def get_booking(session_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT name, people, date, time FROM bookings WHERE session_id = ?",
                           (session_id,)).fetchone()
    return dict(row) if row else None


# ---------------- Events ----------------
def get_events(upcoming_only: bool = False) -> list[dict]:
    """All events sorted by date. upcoming_only=True hides events whose date has passed."""
    sql = "SELECT * FROM events"
    params: tuple = ()
    if upcoming_only:
        sql += " WHERE date >= ?"
        params = (datetime.now().date().isoformat(),)
    with db() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY date, time", params)]
