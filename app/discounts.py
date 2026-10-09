"""
discounts.py - Discount codes created by staff and applied by customers.
"""

import secrets

from app.database import db
from app.data import get_menu


def generate_code(percent: int, max_uses: int, created_by: str) -> str:
    code = "SPICE-" + secrets.token_hex(3).upper()      # e.g. SPICE-4F9A2C
    with db() as conn:
        conn.execute("INSERT INTO discounts VALUES (?, ?, ?, ?)",
                     (code, percent, max_uses, created_by))
    return code


def list_codes() -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM discounts ORDER BY code")]


def delete_code(code: str) -> bool:
    with db() as conn:
        return conn.execute("DELETE FROM discounts WHERE code = ?", (code.upper(),)).rowcount > 0


def apply_code(session_id: str, code: str) -> str | None:
    """Returns an error message, or None if the code was applied."""
    code = code.strip().upper()
    with db() as conn:
        row = conn.execute("SELECT uses_left FROM discounts WHERE code = ?", (code,)).fetchone()
        if not row or row["uses_left"] <= 0:
            return "Invalid or expired discount code"
        current = conn.execute("SELECT code FROM applied_discounts WHERE session_id = ?",
                               (session_id,)).fetchone()
        if current and current["code"] == code:
            return "Code already applied"
        conn.execute("UPDATE discounts SET uses_left = uses_left - 1 WHERE code = ?", (code,))
        conn.execute("INSERT INTO applied_discounts (session_id, code) VALUES (?, ?) "
                     "ON CONFLICT(session_id) DO UPDATE SET code = excluded.code", (session_id, code))
    return None


def calculate_bill(session_id: str, cart: dict) -> dict:
    """Subtotal, discount (if a code was applied) and final total."""
    prices = get_menu(include_unavailable=True)
    subtotal = sum(prices.get(dish, 0) * qty for dish, qty in cart.items())
    with db() as conn:
        row = conn.execute(
            "SELECT a.code, d.percent FROM applied_discounts a "
            "JOIN discounts d ON d.code = a.code WHERE a.session_id = ?", (session_id,)
        ).fetchone()
    code, percent = (row["code"], row["percent"]) if row else (None, 0)
    discount = round(subtotal * percent / 100)
    return {
        "subtotal": subtotal,
        "discount_code": code,
        "discount_percent": percent,
        "discount": discount,
        "total": subtotal - discount,
    }
