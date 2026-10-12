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
            buffet_packages, search_menu, staff_summary, set_dish_availability, update_order_status,
            add_expense]


CHANGE_TOOLS = {"set_dish_availability", "update_order_status", "add_expense"}

SYSTEM_PROMPT = """You are the manager's assistant for the restaurant "{name}". Today is {today}.
Money is in Indian rupees: write it like "Rs. 1,250".

Rules:
- Use the tools to get facts. Never guess numbers, dishes, orders or bookings.
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
    answer = text_of(messages[-1]).strip() or "Done."
    return {"answer": answer, "steps": steps}
