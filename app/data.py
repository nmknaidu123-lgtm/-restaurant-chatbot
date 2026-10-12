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


# ---------------- Events (buffet catering) ----------------
EVENT_STATUSES = ["Enquiry", "Confirmed", "Completed", "Cancelled"]


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    return json.loads(value) if value else []


def get_events(upcoming_only: bool = False) -> list[dict]:
    """All events (for staff) sorted by date, with money totals.
    upcoming_only=True hides events whose date has passed."""
    sql = ("SELECT e.*, COALESCE((SELECT SUM(amount) FROM event_payments p WHERE p.event_id = e.id), 0) "
           "AS payments_total FROM events e")
    params: tuple = ()
    if upcoming_only:
        sql += " WHERE e.date >= ?"
        params = (datetime.now().date().isoformat(),)
    with db() as conn:
        return [event_totals(dict(r)) for r in conn.execute(sql + " ORDER BY e.date, e.time", params)]


def get_event(event_id: int) -> dict | None:
    return next((e for e in get_events() if e["id"] == event_id), None)


def get_public_events() -> list[dict]:
    """Upcoming events customers may see: marked public, and not an enquiry or cancelled.
    Only the name, date, time and description - never prices, guests or phone numbers."""
    return [{k: e[k] for k in ("title", "date", "time", "description")}
            for e in get_events(upcoming_only=True)
            if e.get("is_public") and e.get("status") in ("Confirmed", "Completed")]


def event_totals(e: dict) -> dict:
    """Add the money totals to one buffet event.
    bill       = veg guests x veg price + non-veg guests x non-veg price + extra charges
    investment = veg guests x veg cost  + non-veg guests x non-veg cost  + extra costs
    profit     = bill - investment
    paid       = all payments (advance and later payments)
    balance    = bill - paid  (still to collect)"""
    for key in ("veg_items", "nonveg_items", "menu"):
        e[key] = _json_list(e.get(key))
    veg, nonveg = e.get("veg_guests") or 0, e.get("nonveg_guests") or 0
    food_bill = veg * (e.get("veg_price") or 0) + nonveg * (e.get("nonveg_price") or 0)
    food_cost = veg * (e.get("veg_cost") or 0) + nonveg * (e.get("nonveg_cost") or 0)
    bill = food_bill + (e.get("extra_charges") or 0)
    investment = food_cost + (e.get("extra_cost") or 0)
    paid = (e.get("payments_total") or 0) + (e.get("advance") or 0)
    e.update(guests=veg + nonveg, food_bill=food_bill, total_bill=bill, investment=investment,
             profit=bill - investment, paid=paid, balance=max(bill - paid, 0))
    return e


def get_event_payments(event_id: int) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM event_payments WHERE event_id = ? ORDER BY date, id", (event_id,))]


# ---------------- Buffet packages ----------------
def get_packages() -> list[dict]:
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM event_packages ORDER BY food_type DESC, price_per_head")]
    for r in rows:
        r["items"] = _json_list(r["items"])
    return rows


# ---------------- Kitchen plan for a buffet ----------------
# How much ONE guest eats from each course in a buffet (all items of that course together).
# These are common caterer starting points - adjust from your own experience.
COURSES = [
    ("Starters", ("Veg Starters", "Non-Veg Starters"), 100, "g"),
    ("Soup", ("Soups",), 150, "ml"),
    ("Main course", ("Veg Curries", "Non-Veg Curries"), 150, "g"),
    ("Rice / Biryani", ("Biryani", "Rice and Noodles"), 250, "g"),
    ("Breads", ("Breads",), 2, "pcs"),
    ("South Indian", ("South Indian",), 2, "pcs"),
    ("Dessert", ("Desserts",), 100, "g"),
    ("Drinks", ("Hot Drinks", "Cold Drinks", "Lassi", "Fresh Juices", "Milkshakes", "Mocktails"), 200, "ml"),
]
OTHER_COURSE = ("Other", (), 100, "g")
BUFFER = 1.10            # cook 10% extra so the buffet does not run out


def _course_of(category: str):
    return next((c for c in COURSES if category in c[1]), OTHER_COURSE)


def _amount(value: float, unit: str) -> str:
    if unit == "pcs":
        return f"{int(-(-value // 1))} pcs"
    big, small = ("kg", "g") if unit == "g" else ("litres", "ml")
    return f"{value / 1000:.1f} {big}" if value >= 1000 else f"{int(round(value))} {small}"


def kitchen_plan(e: dict) -> dict:
    """Estimate how much of each dish to cook for a buffet event.
    For every guest group, each course's per-guest amount is shared between the dishes of that
    course: share = amount / n x (1 + 0.1 x (n - 1))  - with more choices people take a little more.
    Then 10% extra is added. If a dish is in both groups, the amounts are added together."""
    category = {r["dish"]: r["category"] for r in get_menu_full()}
    totals: dict[str, dict] = {}
    for guests, items in ((e.get("veg_guests") or 0, e.get("veg_items") or []),
                          (e.get("nonveg_guests") or 0, e.get("nonveg_items") or [])):
        if not guests or not items:
            continue
        by_course: dict[str, list] = {}
        for dish in items:
            course = _course_of(category.get(dish, ""))
            by_course.setdefault(course[0], [course, []])[1].append(dish)
        for (name, _, per_guest, unit), dishes in by_course.values():
            n = len(dishes)
            share = per_guest / n * (1 + 0.1 * (n - 1))
            for dish in dishes:
                row = totals.setdefault(dish, {"dish": dish, "course": name, "unit": unit, "value": 0.0, "guests": 0})
                row["value"] += guests * share * BUFFER
                row["guests"] += guests

    order = [c[0] for c in COURSES] + ["Other"]
    items = sorted(totals.values(), key=lambda r: (order.index(r["course"]), r["dish"]))
    for r in items:
        r["quantity"] = _amount(r.pop("value"), r["unit"])
    guests = (e.get("veg_guests") or 0) + (e.get("nonveg_guests") or 0)
    staff = {"waiters": -(-guests // 25), "cooks": max(1, -(-guests // 60)) if guests else 0,
             "helpers": -(-guests // 40)}
    return {"items": items, "guests": guests, "staff": staff,
            "note": "Estimates for a buffet, with 10% extra. Adjust from your own experience."}
