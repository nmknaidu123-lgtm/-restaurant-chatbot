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


def add_order_notes(session_id: str, new_notes: dict, order_note: str = "") -> None:
    """Add special requests to an existing order.
    new_notes = {"Chicken Biryani": ["1 x Mild, for kids"]}  (one entry per line ordered)"""
    if not new_notes and not order_note:
        return
    with db() as conn:
        row = conn.execute("SELECT notes, order_note FROM orders WHERE session_id = ?",
                           (session_id,)).fetchone()
        if row is None:
            return
        notes = json.loads(row["notes"]) if row["notes"] else {}
        for dish, lines in new_notes.items():
            notes.setdefault(dish, []).extend(lines)
        note = row["order_note"] or ""
        if order_note:
            note = f"{note} | {order_note}" if note else order_note
        conn.execute("UPDATE orders SET notes = ?, order_note = ? WHERE session_id = ?",
                     (json.dumps(notes), note, session_id))


def describe_request(spice: str | None, for_kids: bool, note: str) -> str:
    """Turn the options into one short line for the kitchen, e.g. 'Mild, for baby/kids, no onion'."""
    parts = []
    if spice:
        parts.append(spice)
    if for_kids:
        parts.append("for baby/kids (no chilli, less salt)")
    if note and note.strip():
        parts.append(note.strip())
    return ", ".join(parts)


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
        return [event_totals(dict(r)) for r in conn.execute(sql + " ORDER BY date, time", params)]


def event_totals(e: dict) -> dict:
    """Add the money totals to one event.
    bill       = plates x price per head        (what the customer pays)
    investment = plates x cost per head + extra (what it costs us)
    profit     = bill - investment
    balance    = bill - advance                 (still to collect)"""
    menu = e.get("menu")
    e["menu"] = json.loads(menu) if isinstance(menu, str) and menu else (menu or [])
    plates = e.get("guests") or 0
    bill = plates * (e.get("price_per_head") or 0)
    investment = plates * (e.get("cost_per_head") or 0) + (e.get("extra_cost") or 0)
    e.update(total_bill=bill, investment=investment, profit=bill - investment,
             balance=max(bill - (e.get("advance") or 0), 0))
    return e
