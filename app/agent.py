"""
agent.py - The AI MANAGER ASSISTANT (an AI agent) for the admin page.

How it is different from the customer chatbot (graph.py):
  - graph.py follows a FIXED path: classify the message -> one of 6 nodes.
  - This agent gets a set of TOOLS (normal Python functions). Gemini reads the
    question and DECIDES by itself which tools to call, in what order, and
    then writes the answer. This loop is called a "ReAct" agent:
        think -> call a tool -> read the result -> think -> ... -> answer

Endpoint:  POST /staff/assistant/ask   (admin only)
"""

import datetime as dt
import json
import sqlite3
import warnings
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

with warnings.catch_warnings():            # create_react_agent prints a "moved" notice in new versions
    warnings.simplefilter("ignore")
    from langgraph.prebuilt import create_react_agent

from app.auth import admin_only
from app.database import db
from app.data import get_events, get_menu_full, get_packages, get_setting, kitchen_plan
from app.graph import llm, text_of

router = APIRouter(prefix="/staff/assistant", tags=["Admin - AI assistant"])
memory = MemorySaver()                     # remembers each conversation (until the server restarts)


def _day(day: str) -> dt.date | None:
    """'' -> None (today). 'YYYY-MM-DD' -> date. Anything else -> ValueError."""
    day = (day or "").strip()
    return dt.date.fromisoformat(day) if day else None



# ---------------------------------------------------------------
# ALL-DATA HELPERS - read any date range, past and future (read-only)
# ---------------------------------------------------------------
SECRET_TABLES = {"tokens", "sqlite_sequence"}
SECRET_COLUMNS = {"salt", "password_hash", "password"}


def _range(start: str = "", end: str = "") -> tuple[str, str]:
    return ((start or "").strip()[:10] or "0000-01-01", (end or "").strip()[:10] or "9999-12-31")


def _bill(session_id: str, cart: dict) -> dict:
    """The app's own bill calculator if it works, else menu prices added up."""
    try:
        from app.discounts import calculate_bill
        return calculate_bill(session_id, cart)
    except Exception:
        with db() as conn:
            prices = {r["dish"]: r["price"] for r in conn.execute("SELECT dish, price FROM menu")}
        sub = sum((prices.get(d) or 0) * q for d, q in cart.items())
        return {"subtotal": sub, "discount": 0, "total": sub}


def _overview() -> dict:
    date_cols = {"orders": "updated_at", "events": "date", "bookings": "date",
                 "expenses": "date", "event_payments": "date"}
    out = {}
    with db() as conn:
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if t in SECRET_TABLES:
                continue
            info = {"rows": conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]}
            col = date_cols.get(t)
            if col and info["rows"]:
                first, last = conn.execute(f'SELECT MIN({col}), MAX({col}) FROM "{t}"').fetchone()
                info.update(first_date=(first or "")[:10], last_date=(last or "")[:10])
            out[t] = info
    return {"today": dt.date.today().isoformat(), "tables": out}


def _sales(start: str = "", end: str = "") -> dict:
    start, end = _range(start, end)
    by_day: dict[str, int] = {}
    dishes: dict[str, dict] = {}
    collected = paid = unpaid = unpaid_orders = cancelled = 0
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM orders")]
    for r in rows:
        cart = json.loads(r.get("cart") or "{}")
        total = _bill(r["session_id"], cart)["total"]
        placed = (r.get("updated_at") or "")[:10]
        if r.get("status") == "paid":
            day = (r.get("paid_at") or r.get("updated_at") or "")[:10]
            if start <= day <= end:
                collected += total
                paid += 1
                by_day[day] = by_day.get(day, 0) + total
                for dish, qty in cart.items():
                    d = dishes.setdefault(dish, {"dish": dish, "quantity": 0, "revenue": 0})
                    d["quantity"] += qty
                    d["revenue"] += _bill("", {dish: qty})["subtotal"]
        elif start <= placed <= end:
            if r.get("status") == "cancelled":
                cancelled += 1
            else:
                unpaid_orders += 1
                unpaid += total
    return {"from": start, "to": end, "collected": collected, "paid_orders": paid,
            "average_bill": round(collected / paid) if paid else 0,
            "unpaid_orders": unpaid_orders, "unpaid_amount": unpaid, "cancelled": cancelled,
            "by_day": [{"date": d, "collected": v} for d, v in sorted(by_day.items())][-60:],
            "top_dishes": sorted(dishes.values(), key=lambda x: -x["quantity"])[:10]}


def _expenses(start: str = "", end: str = "") -> dict:
    start, end = _range(start, end)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT date, category, description, amount FROM expenses "
            "WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))]
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + r["amount"]
    return {"from": start, "to": end, "total": sum(by_cat.values()), "by_category": by_cat,
            "expenses": rows[-100:]}


def _events(start: str = "", end: str = "", status: str = "") -> dict:
    start, end = _range(start, end)
    rows = [e for e in get_events() if start <= (e.get("date") or "")[:10] <= end
            and (not status or (e.get("status") or "").lower() == status.strip().lower())]
    keep = ("id", "title", "event_type", "status", "date", "time", "customer", "guests",
            "veg_guests", "nonveg_guests", "veg_package", "nonveg_package",
            "total_bill", "investment", "profit", "paid", "balance")
    live = [e for e in rows if e.get("status") != "Cancelled"]
    return {"from": start, "to": end, "count": len(rows),
            "totals_not_cancelled": {k: sum(e.get(k) or 0 for e in live)
                                     for k in ("guests", "total_bill", "investment", "profit", "paid", "balance")},
            "events": [{k: e.get(k) for k in keep} for e in rows]}


def _profit(start: str = "", end: str = "") -> dict:
    if not (start or "").strip():
        firsts = [v["first_date"] for v in _overview()["tables"].values() if v.get("first_date")]
        start = min(firsts) if firsts else dt.date.today().isoformat()
    end = (end or "").strip() or dt.date.today().isoformat()
    sales, exp = _sales(start, end), _expenses(start, end)
    invest = exp["by_category"].get("Investment (one-time)", 0)
    running = exp["total"] - invest
    days = max((dt.date.fromisoformat(end[:10]) - dt.date.fromisoformat(start[:10])).days + 1, 0)
    with db() as conn:
        wages = conn.execute("SELECT COALESCE(SUM(daily_wage), 0) FROM staff").fetchone()[0]
    return {"from": start[:10], "to": end[:10], "days": days, "sales": sales["collected"],
            "running_costs": running, "labour": wages * days, "daily_wages": wages,
            "restaurant_profit": sales["collected"] - running - wages * days,
            "investment_one_time": invest,
            "event_profit": _events(start, end)["totals_not_cancelled"]["profit"],
            "note": "Labour = today's daily wages x number of days."}


def _orders(start: str = "", end: str = "", status: str = "") -> dict:
    start, end = _range(start, end)
    out = []
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY updated_at")]
    for r in rows:
        if not start <= (r.get("updated_at") or "")[:10] <= end:
            continue
        if status and r.get("status") != status.strip().lower():
            continue
        cart = json.loads(r.get("cart") or "{}")
        out.append({"customer": r["session_id"], "status": r.get("status"), "placed": r.get("updated_at"),
                    "paid_at": r.get("paid_at"), "items": cart, "total": _bill(r["session_id"], cart)["total"]})
    return {"from": start, "to": end, "count": len(out), "orders": out[-100:]}


def _bookings(start: str = "", end: str = "") -> dict:
    start, end = _range(start, end)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM bookings WHERE date BETWEEN ? AND ? ORDER BY date, time", (start, end))]
    return {"from": start, "to": end, "count": len(rows),
            "guests": sum(r.get("people") or 0 for r in rows), "bookings": rows[-100:]}


def _db_file() -> str:
    with db() as conn:
        return next(r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main")


def _tables() -> dict:
    with db() as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        return {t: [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")') if c[1] not in SECRET_COLUMNS]
                for t in names if t not in SECRET_TABLES}


def _query(sql: str) -> dict:
    q = (sql or "").strip().rstrip(";")
    low = q.lower()
    if not (low.startswith("select") or low.startswith("with")) or ";" in q:
        return {"error": "Only one SELECT query is allowed."}
    if any(t in low for t in SECRET_TABLES) or any(c in low for c in SECRET_COLUMNS):
        return {"error": "Passwords and login tokens are private."}
    conn = sqlite3.connect(f"file:{_db_file()}?mode=ro", uri=True)   # READ-ONLY: cannot change anything
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(q).fetchmany(200)]
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()
    return {"count": len(rows), "rows": rows}


# ---------------------------------------------------------------
# TOOLS - the things the agent is allowed to do
# (made inside a function so every tool knows WHICH admin is asking)
# ---------------------------------------------------------------
def make_tools(staff: dict) -> list:
    from app import main as m              # imported here to avoid a circular import

    # ---------- Look things up ----------
    @tool
    def sales_report(day: str = "") -> dict:
        """Sales for one day: money collected, number of orders, average bill, open orders,
        best-selling dishes, order value by hour and the last 7 days.
        day: 'YYYY-MM-DD', or empty for today."""
        return m.sales_report(_day(day), staff)

    @tool
    def money_report(day: str = "") -> dict:
        """Profit for one day: sales, running costs (ingredients, gas, rent...), labour cost
        (daily wages), profit or loss, money invested, and the last 7 days.
        day: 'YYYY-MM-DD', or empty for today."""
        return m.finance_report(_day(day), staff)

    @tool
    def open_orders() -> list:
        """Orders that are not paid or cancelled yet, with items, special requests and totals."""
        rows = m.all_orders(staff)["orders"]
        return [{"customer": o["session_id"], "status": o["status"], "items": o["cart"],
                 "requests": o.get("notes", {}), "note": o.get("order_note", ""), "total": o["total"]}
                for o in rows if o["status"] not in ("paid", "cancelled")]

    @tool
    def bookings(day: str = "") -> list:
        """Table bookings for one day. day: 'YYYY-MM-DD', or empty for today."""
        d = (_day(day) or dt.date.today()).isoformat()
        with db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT name, phone, people, date, time FROM bookings WHERE date = ? ORDER BY time", (d,))]

    @tool
    def upcoming_events() -> list:
        """Upcoming events (buffet parties): status, customer, veg and non-veg guests, packages,
        dishes, total bill, investment (our cost), expected profit, money paid and balance to collect."""
        return [{"id": e["id"], "title": e["title"], "type": e.get("event_type"), "status": e.get("status"),
                 "date": e["date"], "time": e["time"], "customer": e.get("customer"),
                 "veg_guests": e.get("veg_guests", 0), "veg_package": e.get("veg_package"),
                 "veg_dishes": e.get("veg_items", []),
                 "nonveg_guests": e.get("nonveg_guests", 0), "nonveg_package": e.get("nonveg_package"),
                 "nonveg_dishes": e.get("nonveg_items", []),
                 "total_bill": e["total_bill"], "investment": e["investment"], "profit": e["profit"],
                 "paid": e["paid"], "balance": e["balance"]}
                for e in get_events(upcoming_only=True)]

    @tool
    def event_kitchen_plan(event_id: int) -> dict:
        """How much of each dish to cook for an event (buffet estimate with 10% extra),
        and suggested waiters / cooks / helpers. event_id comes from upcoming_events."""
        e = next((x for x in get_events() if x["id"] == event_id), None)
        return kitchen_plan(e) if e else {"error": "No event with that id"}

    @tool
    def buffet_packages() -> list:
        """The buffet packages for events: name, veg or non-veg, price per head, our cost per head, dishes."""
        return get_packages()

    @tool
    def search_menu(query: str = "") -> list:
        """Find dishes on the menu (name, price, category, veg or non-veg, available or sold out).
        query: part of a dish or category name, or empty for the whole menu."""
        q = query.strip().lower()
        return [{"dish": r["dish"], "price": r["price"], "category": r["category"],
                 "veg": bool(r.get("is_veg")), "available": bool(r["available"])}
                for r in get_menu_full() if not q or q in r["dish"].lower() or q in r["category"].lower()]

    @tool
    def staff_summary() -> dict:
        """How many staff work in each position, and the total daily wages (labour cost per day)."""
        people = m.list_staff(staff)
        positions: dict[str, int] = {}
        for p in people:
            positions[p.get("position") or "Not set"] = positions.get(p.get("position") or "Not set", 0) + 1
        return {"total_staff": len(people), "by_position": positions,
                "daily_wages_total": sum(p.get("daily_wage") or 0 for p in people)}

    # ---------- ALL data, any dates (past and future) ----------
    @tool
    def data_overview() -> dict:
        """What data exists: rows in every table and the FIRST and LAST date recorded.
        Call this first for any question about old, past, total or 'all' data."""
        return _overview()

    @tool
    def sales_between(start: str = "", end: str = "") -> dict:
        """Sales collected (paid orders) between two dates, per day, best dishes, unpaid orders.
        start/end: 'YYYY-MM-DD'. Empty start = from the very beginning, empty end = until now."""
        return _sales(start, end)

    @tool
    def expenses_between(start: str = "", end: str = "") -> dict:
        """Every expense between two dates, with totals per category.
        start/end: 'YYYY-MM-DD'. Empty = all time."""
        return _expenses(start, end)

    @tool
    def profit_between(start: str = "", end: str = "") -> dict:
        """Profit for ANY period (week, month, all time): sales - running costs - labour,
        plus one-time investment and event profit shown separately.
        start/end: 'YYYY-MM-DD'. Empty start = from the first record, empty end = today."""
        return _profit(start, end)

    @tool
    def events_between(start: str = "", end: str = "", status: str = "") -> dict:
        """ALL events, PAST and future, between two dates: bill, investment, profit, paid, balance.
        start/end: 'YYYY-MM-DD' (empty = all time). status: Enquiry, Confirmed, Completed,
        Cancelled, or empty for all. Use this for past events."""
        return _events(start, end, status)

    @tool
    def orders_between(start: str = "", end: str = "", status: str = "") -> dict:
        """Orders placed between two dates. status: new, preparing, served, paid, cancelled, or empty.
        start/end: 'YYYY-MM-DD' (empty = all time)."""
        return _orders(start, end, status)

    @tool
    def bookings_between(start: str = "", end: str = "") -> dict:
        """Table bookings between two dates (past or future). start/end: 'YYYY-MM-DD' (empty = all)."""
        return _bookings(start, end)

    @tool
    def database_tables() -> dict:
        """The database tables and their columns. Use before ask_database."""
        return _tables()

    @tool
    def ask_database(sql: str) -> dict:
        """For any question the other tools cannot answer: run ONE read-only SQLite SELECT query
        (max 200 rows). It can never change data. Use database_tables to see the columns."""
        return _query(sql)

    # ---------- Change things ----------
    @tool
    def set_dish_availability(dish: str, available: bool) -> str:
        """Mark a dish as sold out (available=False) or available again (available=True).
        dish: the dish name (use search_menu first if you are not sure of the exact name)."""
        match = next((r["dish"] for r in get_menu_full() if r["dish"].lower() == dish.strip().lower()), None)
        if not match:
            return f"No dish called '{dish}'. Use search_menu to find the exact name."
        m.set_availability(match, m.Availability(available=available), staff)
        return f"{match} is now {'available' if available else 'SOLD OUT'}."

    @tool
    def update_order_status(customer: str,
                            status: Literal["new", "preparing", "served", "paid", "cancelled"]) -> str:
        """Change an order's status. customer: the order's customer id exactly as open_orders shows it
        (for example 'table-5')."""
        try:
            m.update_status(customer.strip(), m.StatusUpdate(status=status), staff)
        except HTTPException as e:
            return f"Could not update: {e.detail}"
        return f"Order {customer} is now '{status}'."

    @tool
    def add_expense(category: Literal["Ingredients", "Gas and electricity", "Rent", "Maintenance",
                                      "Extra labour", "Other running cost", "Investment (one-time)"],
                    amount: int, description: str = "", day: str = "") -> str:
        """Record money spent. amount in Rs. (whole number). day: 'YYYY-MM-DD', or empty for today."""
        body = m.ExpenseIn(date=_day(day) or dt.date.today(), category=category,
                           description=description, amount=amount)
        return m.add_expense(body, staff)["message"]

    return [sales_report, money_report, open_orders, bookings, upcoming_events, event_kitchen_plan,
            buffet_packages, search_menu, staff_summary,
            data_overview, sales_between, expenses_between, profit_between, events_between,
            orders_between, bookings_between, database_tables, ask_database,
            set_dish_availability, update_order_status, add_expense]


CHANGE_TOOLS = {"set_dish_availability", "update_order_status", "add_expense"}

SYSTEM_PROMPT = """You are the manager's assistant for the restaurant "{name}". Today is {today}.
Money is in Indian rupees: write it like "Rs. 1,250".

Rules:
- Use the tools to get facts. Never guess numbers, dishes, orders or bookings.
- You can see ALL the restaurant's data, past and future. Never say "the system only tracks
  upcoming events" or "no data" before checking with data_overview.
- For any period other than one day (this week, last month, past events, all time, a date range)
  use the *_between tools with start and end dates. Work out the dates from today's date.
  Example: "this week" = Monday of this week to today.
- If a report for today or this week is empty, say so AND tell the manager the last date
  that has data (from data_overview), and offer to show that.
- Keep answers short and practical: a few sentences or a short list. Give advice when it helps
  (for example: what to restock, which hours are busy, whether labour cost is too high).
- You can change data with set_dish_availability, update_order_status and add_expense.
  Only change something when the manager clearly asks. If the request is unclear
  (which dish? how much money?), ask first. After a change, say exactly what you changed.
- Reply in the same language the manager uses (English or Telugu)."""


# ---------------------------------------------------------------
# ENDPOINT
# ---------------------------------------------------------------
class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000, examples=["How was business today?"])
    chat_id: str = Field(default="main", min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/ask")
def ask(body: AskIn, staff: dict = Depends(admin_only)):
    agent = create_react_agent(
        llm,
        make_tools(staff),
        prompt=SYSTEM_PROMPT.format(name=get_setting("restaurant_name"),
                                    today=dt.date.today().strftime("%A, %d %B %Y")),
        checkpointer=memory,
    )
    config = {"configurable": {"thread_id": f"{staff['username']}:{body.chat_id}"},
              "recursion_limit": 16}
    try:
        result = agent.invoke({"messages": [HumanMessage(body.message)]}, config=config)
    except Exception as e:                  # Gemini busy, bad API key, too many steps...
        raise HTTPException(status_code=502,
                            detail=f"The AI could not answer right now ({type(e).__name__}). "
                                   "Check the Gemini API key, or try again in a minute.")

    # Find what the agent did in THIS turn (everything after the last question)
    messages = result["messages"]
    start = max(i for i, msg in enumerate(messages) if isinstance(msg, HumanMessage))
    steps = []
    for msg in messages[start + 1:]:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                steps.append({"tool": call["name"], "args": call.get("args", {}),
                              "changed_data": call["name"] in CHANGE_TOOLS})
    if any(st["changed_data"] for st in steps):    # the AI changed data -> save a backup now
        try:
            from app.backup import make_backup
            make_backup("ai-change")
        except Exception as e:                     # never break the answer because of a backup
            print(f"[backup] Could not back up after AI change: {e}")
    answer = text_of(messages[-1]).strip() or "Done."
    return {"answer": answer, "steps": steps}