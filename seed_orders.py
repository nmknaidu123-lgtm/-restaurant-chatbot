"""
seed_orders.py - Adds 30 SAMPLE customer orders to restaurant.db (for testing / demo).

Run:   python seed_orders.py
Running it again does not create duplicates.
The orders use dishes from your current menu, so add dishes first if the menu is empty.
"""

import json
import random
from datetime import datetime, timedelta

from app.database import db, init_db

HOW_MANY = 30
# Most orders are still in progress; a few are finished or cancelled
STATUSES = ["new"] * 6 + ["preparing"] * 8 + ["served"] * 7 + ["paid"] * 7 + ["cancelled"] * 2


def main() -> None:
    init_db()
    random.seed(7)                                   # same sample data every time

    with db() as conn:
        dishes = [r["dish"] for r in conn.execute("SELECT dish FROM menu WHERE available = 1")]
        if not dishes:
            print("Your menu is empty - add some dishes first.")
            return

        added = skipped = 0
        now = datetime.now()
        for i in range(1, HOW_MANY + 1):
            # Half are dine-in tables, half are online orders
            session_id = f"table-{i:02d}" if i % 2 else f"online-{1000 + i}"
            if conn.execute("SELECT 1 FROM orders WHERE session_id = ?", (session_id,)).fetchone():
                skipped += 1
                continue

            cart = {}
            for dish in random.sample(dishes, k=random.randint(1, 5)):
                cart[dish] = random.randint(1, 3)

            status = STATUSES[(i - 1) % len(STATUSES)]
            when = now - timedelta(minutes=random.randint(5, 600))   # sometime in the last 10 hours
            conn.execute(
                "INSERT INTO orders (session_id, cart, status, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, json.dumps(cart), status, when.isoformat(timespec="seconds")),
            )
            added += 1

        total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    print(f"Added {added} sample orders ({skipped} already existed).")
    print(f"Total orders in database: {total}")


if __name__ == "__main__":
    main()
