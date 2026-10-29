"""
discounts.py - Discount codes created by staff and applied by customers,
and the bill calculator (subtotal - discount = total).
"""

import secrets

from app.database import db
from app.data import get_menu


# ---------------------------------------------------------------- helpers
def _columns(conn, table: str) -> list[str]:
    """Column names of a table, in order (so this file works with your existing database)."""
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _discount_cols(conn) -> dict:
    """The discounts table has 4 columns, in this order: code, percent, uses left, created by."""
    cols = _columns(conn, "discounts")
    names = ["code", "percent", "uses_left", "created_by"]
    return {names[i]: cols[i] for i in range(min(len(cols), 4))}


def _applied_cols(conn) -> dict:
    """Find the session and code columns of applied_discounts (create the table if missing)."""
    conn.execute("CREATE TABLE IF NOT EXISTS applied_discounts (session_id TEXT PRIMARY KEY, code TEXT)")
    cols = _columns(conn, "applied_discounts")
    session = next((c for c in cols if "session" in c.lower()), cols[0])
    code = next((c for c in cols if "code" in c.lower()), cols[1] if len(cols) > 1 else cols[0])
    return {"all": cols, "session": session, "code": code}


# ---------------------------------------------------------------- staff
def generate_code(percent: int, max_uses: int, created_by: str) -> str:
    code = "SPICE-" + secrets.token_hex(3).upper()      # e.g. SPICE-4F9A2C
    with db() as conn:
        conn.execute("INSERT INTO discounts VALUES (?, ?, ?, ?)",
                     (code, percent, max_uses, created_by))
    return code


def list_codes() -> list[dict]:
    """Every discount code: code, percent, uses left, who created it."""
    with db() as conn:
        c = _discount_cols(conn)
        rows = conn.execute("SELECT * FROM discounts").fetchall()
    out = []
    for r in rows:
        r = dict(r)
        out.append({key: r.get(col) for key, col in c.items()})
    return out


def delete_code(code: str) -> bool:
    """Delete a code. Returns True if it existed."""
    with db() as conn:
        c = _discount_cols(conn)
        return conn.execute(f'DELETE FROM discounts WHERE UPPER("{c["code"]}") = ?',
                            (code.strip().upper(),)).rowcount > 0


# ---------------------------------------------------------------- customers
def apply_code(session_id: str, code: str) -> str | None:
    """Use a code on a customer's order. Returns an error message, or None if it worked."""
    code = (code or "").strip().upper()
    if not code:
        return "Please enter a discount code."
    with db() as conn:
        c = _discount_cols(conn)
        a = _applied_cols(conn)
        row = conn.execute(f'SELECT * FROM discounts WHERE UPPER("{c["code"]}") = ?', (code,)).fetchone()
        if row is None:
            return "This discount code does not exist."
        row = dict(row)
        if (row.get(c["uses_left"]) or 0) <= 0:
            return "This discount code has already been used up."
        if not conn.execute("SELECT 1 FROM orders WHERE session_id = ?", (session_id,)).fetchone():
            return "Please order something first, then apply the code."
        if conn.execute(f'SELECT 1 FROM applied_discounts WHERE "{a["session"]}" = ?',
                        (session_id,)).fetchone():
            return "A discount is already applied to this order."

        values = {a["session"]: session_id, a["code"]: row[c["code"]]}
        if "percent" in a["all"]:
            values["percent"] = row[c["percent"]]
        cols = ", ".join(f'"{k}"' for k in values)
        conn.execute(f"INSERT INTO applied_discounts ({cols}) VALUES ({', '.join('?' * len(values))})",
                     tuple(values.values()))
        conn.execute(f'UPDATE discounts SET "{c["uses_left"]}" = "{c["uses_left"]}" - 1 '
                     f'WHERE "{c["code"]}" = ?', (row[c["code"]],))
    return None


def _applied_discount(session_id: str) -> tuple[str, int]:
    """(code, percent) applied to this customer's order, or ("", 0)."""
    if not session_id:
        return "", 0
    with db() as conn:
        c = _discount_cols(conn)
        a = _applied_cols(conn)
        applied = conn.execute(f'SELECT * FROM applied_discounts WHERE "{a["session"]}" = ?',
                               (session_id,)).fetchone()
        if applied is None:
            return "", 0
        applied = dict(applied)
        code = applied.get(a["code"]) or ""
        if applied.get("percent") is not None:
            return code, int(applied["percent"])
        row = conn.execute(f'SELECT "{c["percent"]}" FROM discounts WHERE UPPER("{c["code"]}") = ?',
                           (str(code).upper(),)).fetchone()
    return code, int(row[0]) if row else 0


def calculate_bill(session_id: str, cart: dict) -> dict:
    """The bill for a cart: subtotal (menu prices x quantity), discount and total, in Rs."""
    prices = get_menu(include_unavailable=True)     # include sold-out dishes already ordered
    subtotal = sum((prices.get(dish) or 0) * qty for dish, qty in (cart or {}).items())
    code, percent = _applied_discount(session_id)
    discount = round(subtotal * percent / 100)
    return {"subtotal": subtotal, "discount_code": code, "discount_percent": percent,
            "discount": discount, "total": subtotal - discount}
