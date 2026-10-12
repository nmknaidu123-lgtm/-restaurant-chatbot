"""
main.py - The FastAPI backend (all endpoints).

Run:    uvicorn app.main:api --reload
Admin:  http://127.0.0.1:8000/admin   (manage everything in one place)
API:    http://127.0.0.1:8000/docs
"""

import json
import secrets
import datetime as dt
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.database import db, init_db, new_password_record
from app.data import (get_setting, set_setting, get_menu, get_menu_by_category, get_menu_full,
                      get_order_cart, save_order, get_order_status, save_booking, get_booking,
                      add_order_notes, describe_request,
                      get_events, get_event, get_public_events, get_event_payments, get_packages,
                      kitchen_plan, EVENT_STATUSES)
from app.database import guess_is_veg
from app.graph import bot, session_state, clear_session
from app.discounts import generate_code, list_codes, delete_code, apply_code, calculate_bill
from app.auth import router as auth_router, current_staff, admin_only

init_db()      # creates restaurant.db with default data the first time

api = FastAPI(title="Restaurant Chatbot API")
api.include_router(auth_router)          # /staff/login, /staff/logout, /staff/me
from app.agent import router as agent_router   # noqa: E402  (AI manager assistant)
api.include_router(agent_router)         # /staff/assistant/ask

ADMIN_PAGE = Path(__file__).resolve().parent.parent / "static" / "admin.html"


# =========================================================
# CUSTOMER ENDPOINTS (no login)
# =========================================================
class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=50, examples=["customer1"])
    message: str = Field(min_length=1, max_length=500, examples=["2 chicken biryani and 1 lassi"])


class ChatResponse(BaseModel):
    session_id: str
    intent: str
    reply: str


class ApplyDiscountRequest(BaseModel):
    session_id: str = Field(examples=["customer1"])
    code: str = Field(examples=["SPICE-AB12CD"])


@api.get("/", tags=["Customer"])
def home():
    return {"message": f"Welcome to {get_setting('restaurant_name')}!",
            "admin_page": "/admin", "api_docs": "/docs"}


@api.get("/menu", tags=["Customer"])
def menu():
    return get_menu_by_category()


@api.post("/chat", response_model=ChatResponse, tags=["Customer"])
def chat(req: ChatRequest):
    config = {"configurable": {"thread_id": req.session_id}}
    inputs = {"user_input": req.message, "session_id": req.session_id, "new_notes": {}}
    # After a server restart the chatbot's memory is empty - reload the saved cart
    if not session_state(req.session_id).get("cart"):
        saved_cart = get_order_cart(req.session_id)
        if saved_cart:
            inputs["cart"] = saved_cart

    result = bot.invoke(inputs, config=config)

    if result.get("cart"):
        save_order(req.session_id, result["cart"])
        add_order_notes(req.session_id, result.get("new_notes") or {})
    if result.get("booking") and not result.get("awaiting_booking"):
        save_booking(req.session_id, result["booking"])
    return ChatResponse(session_id=req.session_id, intent=result["intent"], reply=result["reply"])


@api.get("/bill/{session_id}", tags=["Customer"])
def bill(session_id: str):
    cart = get_order_cart(session_id)
    return {"session_id": session_id, "cart": cart, **calculate_bill(session_id, cart),
            "status": get_order_status(session_id), "booking": get_booking(session_id)}


@api.post("/apply-discount", tags=["Customer"])
def apply_discount(req: ApplyDiscountRequest):
    error = apply_code(req.session_id, req.code)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"message": "Discount applied!",
            "bill": calculate_bill(req.session_id, get_order_cart(req.session_id))}


@api.get("/graph", tags=["Customer"])
def graph():
    return {"mermaid": bot.get_graph().draw_mermaid()}


@api.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(ADMIN_PAGE)


# =========================================================
# STAFF: ORDERS AND BOOKINGS (any staff)
# =========================================================
class StatusUpdate(BaseModel):
    status: Literal["new", "preparing", "served", "paid", "cancelled"]


@api.get("/staff/orders", tags=["Staff - Orders"])
def all_orders(staff: dict = Depends(current_staff)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY updated_at DESC").fetchall()
    orders = []
    for r in rows:
        cart = json.loads(r["cart"])
        orders.append({"session_id": r["session_id"], "cart": cart, "status": r["status"],
                       "updated_at": r["updated_at"],
                       "notes": json.loads(r["notes"]) if r["notes"] else {},
                       "order_note": r["order_note"] or "",
                       **calculate_bill(r["session_id"], cart)})
    return {"count": len(orders), "orders": orders}


@api.put("/staff/orders/{session_id}/status", tags=["Staff - Orders"])
def update_status(session_id: str, body: StatusUpdate, staff: dict = Depends(current_staff)):
    now = dt.datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        # paid_at = the moment it became paid; cleared if the status moves away from paid
        changed = conn.execute(
            "UPDATE orders SET status = ?, paid_at = CASE WHEN ? = 'paid' "
            "THEN COALESCE(paid_at, ?) ELSE NULL END WHERE session_id = ?",
            (body.status, body.status, now, session_id)).rowcount
    if not changed:
        raise HTTPException(status_code=404, detail="No order for this session")
    return {"session_id": session_id, "status": body.status, "updated_by": staff["username"]}


class OrderLine(BaseModel):
    dish: str = Field(min_length=1, max_length=60, examples=["Chicken Biryani"])
    quantity: int = Field(ge=1, le=100, examples=[2])
    spice: Optional[Literal["Mild", "Medium", "Spicy", "Extra spicy"]] = None
    for_kids: bool = Field(default=False, description="For a baby / child: no chilli, less salt")
    note: str = Field(default="", max_length=100, examples=["no onion"])


class StaffOrder(BaseModel):
    customer: str = Field(min_length=1, max_length=40, examples=["Table 5"],
                          description="Table number or customer name")
    items: list[OrderLine] = Field(min_length=1, max_length=50)
    order_note: str = Field(default="", max_length=200, examples=["Serve starters first"],
                            description="A note for the whole order")


@api.post("/staff/orders", tags=["Staff - Orders"])
def take_order(body: StaffOrder, staff: dict = Depends(current_staff)):
    """A waiter takes an order (dine-in table or phone). Adds to the table's open order if there is one."""
    menu = get_menu()                       # only dishes that are available now
    unknown = [line.dish for line in body.items if line.dish not in menu]
    if unknown:
        raise HTTPException(status_code=400,
                            detail="Not on the menu or sold out: " + ", ".join(unknown))

    # "Table 5" -> "table-5"
    session_id = "-".join("".join(c if c.isalnum() else " " for c in body.customer.lower()).split())
    if not session_id:
        raise HTTPException(status_code=400, detail="Enter a table number or customer name")

    with db() as conn:
        row = conn.execute("SELECT cart, status FROM orders WHERE session_id = ?", (session_id,)).fetchone()
    if row and row["status"] in ("paid", "cancelled"):
        session_id = f"{session_id}-{secrets.token_hex(2)}"   # finished order: start a new one
        row = None

    cart = json.loads(row["cart"]) if row else {}
    notes: dict[str, list[str]] = {}
    for line in body.items:
        cart[line.dish] = cart.get(line.dish, 0) + line.quantity
        request = describe_request(line.spice, line.for_kids, line.note)
        if request:
            notes.setdefault(line.dish, []).append(f"{line.quantity} x {request}")
    save_order(session_id, cart)
    add_order_notes(session_id, notes, body.order_note.strip())
    return {"message": f"Order saved for {body.customer.strip()}", "session_id": session_id,
            "cart": cart, **calculate_bill(session_id, cart), "taken_by": staff["username"]}


@api.get("/staff/sales", tags=["Staff - Sales"])
def sales_report(day: Optional[dt.date] = None, staff: dict = Depends(current_staff)):
    """How business is going on one day (default: today), plus the 7 days up to it."""
    day = day or dt.date.today()
    d = day.isoformat()
    with db() as conn:
        rows = conn.execute("SELECT * FROM orders").fetchall()
        bookings = conn.execute("SELECT people FROM bookings WHERE date = ?", (d,)).fetchall()
        events = conn.execute("SELECT guests FROM events WHERE date = ?", (d,)).fetchall()

    week = [(day - dt.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    collected_by_day = {w: 0 for w in week}
    by_hour = [0] * 24
    dishes: dict[str, dict] = {}
    orders = paid = open_orders = cancelled = 0
    collected = open_amount = items_sold = 0

    for r in rows:
        cart = json.loads(r["cart"])
        total = calculate_bill(r["session_id"], cart)["total"]
        placed = r["updated_at"] or ""
        paid_on = (r["paid_at"] or placed) if r["status"] == "paid" else ""

        if paid_on[:10] in collected_by_day:            # sales collected, by day paid
            collected_by_day[paid_on[:10]] += total
        if paid_on[:10] == d:
            paid += 1
            collected += total

        if placed[:10] != d:                            # the rest is about orders placed today
            continue
        orders += 1
        if r["status"] == "cancelled":
            cancelled += 1
            continue
        if r["status"] != "paid":
            open_orders += 1
            open_amount += total
        if len(placed) >= 13 and placed[11:13].isdigit():
            by_hour[int(placed[11:13])] += total
        for dish, qty in cart.items():
            items_sold += qty
            entry = dishes.setdefault(dish, {"dish": dish, "quantity": 0, "revenue": 0})
            entry["quantity"] += qty
            entry["revenue"] += calculate_bill("", {dish: qty})["subtotal"]

    top = sorted(dishes.values(), key=lambda x: (-x["quantity"], -x["revenue"]))[:8]
    return {
        "date": d,
        "collected": collected, "paid_orders": paid,
        "average_bill": round(collected / paid) if paid else 0,
        "orders": orders, "open_orders": open_orders, "open_amount": open_amount,
        "cancelled": cancelled, "items_sold": items_sold,
        "bookings": len(bookings), "booked_guests": sum(b["people"] or 0 for b in bookings),
        "event_plates": sum(e["guests"] or 0 for e in events),
        "top_dishes": top,
        "by_hour": [{"hour": h, "amount": a} for h, a in enumerate(by_hour)],
        "last_7_days": [{"date": w, "collected": collected_by_day[w]} for w in week],
    }


@api.get("/staff/bookings", tags=["Staff - Orders"])
def all_bookings(staff: dict = Depends(current_staff)):
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM bookings ORDER BY date, time")]
    return {"count": len(rows), "bookings": rows}


class PhoneBooking(BaseModel):
    name: str = Field(min_length=1, max_length=60, examples=["Ravi Kumar"])
    phone: str = Field(default="", max_length=20, examples=["9876543210"])
    people: int = Field(ge=1, le=500, examples=[4])
    date: dt.date = Field(examples=["2026-10-12"], description="YYYY-MM-DD")
    time: str = Field(min_length=1, max_length=30, examples=["8 PM"])


@api.post("/staff/bookings", tags=["Staff - Orders"])
def add_booking(body: PhoneBooking, staff: dict = Depends(current_staff)):
    """Staff book a table for a customer who called the restaurant."""
    session_id = "phone-" + secrets.token_hex(3)          # e.g. phone-4f9a2c
    with db() as conn:
        conn.execute(
            "INSERT INTO bookings (session_id, name, people, date, time, phone) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, body.name.strip(), body.people, body.date.isoformat(),
             body.time.strip(), body.phone.strip()),
        )
    return {"message": f"Table booked for {body.name.strip()}, {body.people} people",
            "session_id": session_id, "booked_by": staff["username"]}


@api.delete("/staff/bookings/{session_id}", tags=["Staff - Orders"])
def delete_booking(session_id: str, staff: dict = Depends(current_staff)):
    with db() as conn:
        if not conn.execute("DELETE FROM bookings WHERE session_id = ?", (session_id,)).rowcount:
            raise HTTPException(status_code=404, detail="Booking not found")
    return {"message": "Booking cancelled"}


@api.delete("/staff/customers/{session_id}", tags=["Staff - Orders"])
def remove_customer(session_id: str, staff: dict = Depends(current_staff)):
    """Remove a customer's chat: conversation memory, order, booking and applied discount."""
    with db() as conn:
        removed = 0
        for table in ("orders", "bookings", "applied_discounts"):
            removed += conn.execute(f"DELETE FROM {table} WHERE session_id = ?",
                                    (session_id,)).rowcount
    had_chat = bool(session_state(session_id))
    clear_session(session_id)
    if not removed and not had_chat:
        raise HTTPException(status_code=404, detail="Customer not found")
    return {"message": f"Chat for {session_id} removed", "removed_by": staff["username"]}


# =========================================================
# STAFF: MENU
# =========================================================
class MenuItem(BaseModel):
    dish: str = Field(min_length=1, max_length=60, examples=["Mango Lassi"])
    price: int = Field(gt=0, le=100000, examples=[90])
    category: str = Field(min_length=1, max_length=40, examples=["Lassi"])
    available: bool = True
    is_veg: Optional[bool] = Field(default=None, description="Veg dish? Leave empty to guess from the name")


class Availability(BaseModel):
    available: bool


@api.get("/staff/menu", tags=["Staff - Menu"])
def menu_full(staff: dict = Depends(current_staff)):
    return get_menu_full()


@api.put("/staff/menu", tags=["Staff - Menu"])
def add_or_update_dish(item: MenuItem, staff: dict = Depends(admin_only)):
    dish = item.dish.strip()
    with db() as conn:
        exists = conn.execute("SELECT 1 FROM menu WHERE dish = ?", (dish,)).fetchone()
        is_veg = item.is_veg if item.is_veg is not None else guess_is_veg(dish, item.category)
        conn.execute(
            "INSERT INTO menu (dish, price, category, available, is_veg) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(dish) DO UPDATE SET price = excluded.price, category = excluded.category, "
            "available = excluded.available, is_veg = excluded.is_veg",
            (dish, item.price, item.category.strip(), int(item.available), int(is_veg)),
        )
    return {"message": f"{dish} {'updated' if exists else 'added'}"}


@api.patch("/staff/menu/{dish}/availability", tags=["Staff - Menu"])
def set_availability(dish: str, body: Availability, staff: dict = Depends(current_staff)):
    """Any staff can mark a dish as sold out / available again."""
    with db() as conn:
        if not conn.execute("UPDATE menu SET available = ? WHERE dish = ?",
                            (int(body.available), dish)).rowcount:
            raise HTTPException(status_code=404, detail="Dish not found")
    return {"dish": dish, "available": body.available}


@api.delete("/staff/menu/{dish}", tags=["Staff - Menu"])
def delete_dish(dish: str, staff: dict = Depends(admin_only)):
    with db() as conn:
        if not conn.execute("DELETE FROM menu WHERE dish = ?", (dish,)).rowcount:
            raise HTTPException(status_code=404, detail="Dish not found")
    return {"message": f"{dish} deleted"}


# =========================================================
# STAFF: DISCOUNT CODES
# =========================================================
class DiscountRequest(BaseModel):
    percent: int = Field(ge=1, le=50, examples=[10], description="1 to 50 percent")
    max_uses: int = Field(default=1, ge=1, le=1000, examples=[5])


@api.post("/staff/discounts", tags=["Staff - Discounts"])
def create_discount(body: DiscountRequest, staff: dict = Depends(current_staff)):
    if body.percent > 20 and staff["role"] != "admin":
        raise HTTPException(status_code=403, detail="Staff can create up to 20% off. Ask an admin.")
    code = generate_code(body.percent, body.max_uses, staff["username"])
    return {"code": code, "percent": body.percent, "uses_left": body.max_uses}


@api.get("/staff/discounts", tags=["Staff - Discounts"])
def all_discounts(staff: dict = Depends(current_staff)):
    return list_codes()


@api.delete("/staff/discounts/{code}", tags=["Staff - Discounts"])
def remove_discount(code: str, staff: dict = Depends(admin_only)):
    if not delete_code(code):
        raise HTTPException(status_code=404, detail="Code not found")
    return {"message": f"{code.upper()} deleted"}


# =========================================================
# ADMIN: STAFF ACCOUNTS
# =========================================================
class NewStaff(BaseModel):
    username: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_.-]+$",
                          examples=["ravi"])
    password: str = Field(min_length=6, max_length=100, examples=["ravi@2026"])
    role: Literal["staff", "admin"] = "staff"
    full_name: str = Field(default="", max_length=60, examples=["Ravi Kumar"])
    position: str = Field(default="", max_length=40, examples=["Waiter"])
    phone: str = Field(default="", max_length=20, examples=["9876543210"])
    shift: str = Field(default="", max_length=30, examples=["Morning"])


class StaffUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=6, max_length=100)
    role: Optional[Literal["staff", "admin"]] = None
    daily_wage: Optional[int] = Field(default=None, ge=0, le=100000, description="Rs. per day")


@api.get("/staff/users", tags=["Admin - Staff accounts"])
def list_staff(staff: dict = Depends(admin_only)):
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT username, role, full_name, position, phone, shift, daily_wage FROM staff "
            "ORDER BY role, position, full_name, username")]


@api.post("/staff/users", tags=["Admin - Staff accounts"])
def add_staff(body: NewStaff, staff: dict = Depends(admin_only)):
    salt, pw_hash = new_password_record(body.password)
    with db() as conn:
        if conn.execute("SELECT 1 FROM staff WHERE username = ?", (body.username,)).fetchone():
            raise HTTPException(status_code=400, detail="Username already exists")
        conn.execute(
            "INSERT INTO staff (username, salt, password_hash, role, full_name, position, phone, shift) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (body.username, salt, pw_hash, body.role, body.full_name.strip(),
             body.position.strip(), body.phone.strip(), body.shift.strip()),
        )
    return {"message": f"{body.username} added as {body.role}"}


@api.put("/staff/users/{username}", tags=["Admin - Staff accounts"])
def update_staff(username: str, body: StaffUpdate, staff: dict = Depends(admin_only)):
    if body.password is None and body.role is None and body.daily_wage is None:
        raise HTTPException(status_code=400, detail="Nothing to update")
    if username == staff["username"] and body.role == "staff":
        raise HTTPException(status_code=400, detail="You cannot remove your own admin role")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM staff WHERE username = ?", (username,)).fetchone():
            raise HTTPException(status_code=404, detail="User not found")
        if body.password:
            salt, pw_hash = new_password_record(body.password)
            conn.execute("UPDATE staff SET salt = ?, password_hash = ? WHERE username = ?",
                         (salt, pw_hash, username))
        if body.role:
            conn.execute("UPDATE staff SET role = ? WHERE username = ?", (body.role, username))
        if body.daily_wage is not None:
            conn.execute("UPDATE staff SET daily_wage = ? WHERE username = ?", (body.daily_wage, username))
        # Log this user out everywhere so a password or role change takes effect at once
        if username != staff["username"] and (body.password or body.role):
            conn.execute("DELETE FROM tokens WHERE username = ?", (username,))
    return {"message": f"{username} updated"}


@api.delete("/staff/users/{username}", tags=["Admin - Staff accounts"])
def delete_staff(username: str, staff: dict = Depends(admin_only)):
    if username == staff["username"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    with db() as conn:
        if not conn.execute("DELETE FROM staff WHERE username = ?", (username,)).rowcount:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute("DELETE FROM tokens WHERE username = ?", (username,))
    return {"message": f"{username} deleted"}


# =========================================================
# ADMIN: RESTAURANT SETTINGS
# =========================================================
class Settings(BaseModel):
    restaurant_name: str = Field(min_length=1, max_length=60)
    restaurant_info: str = Field(max_length=2000)


@api.get("/staff/settings", tags=["Admin - Settings"])
def read_settings(staff: dict = Depends(current_staff)):
    return {"restaurant_name": get_setting("restaurant_name"),
            "restaurant_info": get_setting("restaurant_info")}


@api.put("/staff/settings", tags=["Admin - Settings"])
def save_settings(body: Settings, staff: dict = Depends(admin_only)):
    set_setting("restaurant_name", body.restaurant_name.strip())
    set_setting("restaurant_info", body.restaurant_info.strip())
    return {"message": "Settings saved"}


# =========================================================
# EVENTS - buffet catering (customers see public events, staff manage everything)
# =========================================================
EVENT_TYPES = ["Birthday", "Wedding", "Engagement", "Corporate", "Get-together", "Festival / special night", "Other"]


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=80, examples=["Ravi's 40th birthday"])
    event_type: Literal["Birthday", "Wedding", "Engagement", "Corporate", "Get-together",
                        "Festival / special night", "Other"] = "Other"
    status: Literal["Enquiry", "Confirmed", "Completed", "Cancelled"] = "Confirmed"
    date: dt.date = Field(examples=["2026-10-17"], description="YYYY-MM-DD")
    time: str = Field(default="", max_length=30, examples=["7 PM"])
    description: str = Field(default="", max_length=500, examples=["Garden area, cake at 9 PM"])
    customer: str = Field(default="", max_length=60, examples=["Ravi Kumar"])
    phone: str = Field(default="", max_length=20, examples=["9876543210"])
    is_public: bool = Field(default=False, description="Show this event to customers in the chatbot")
    # Veg guests
    veg_guests: int = Field(default=0, ge=0, le=10000, examples=[80])
    veg_package: str = Field(default="", max_length=60, examples=["Veg Classic"])
    veg_price: int = Field(default=0, ge=0, le=100000, examples=[350], description="Rs. per veg guest")
    veg_cost: int = Field(default=0, ge=0, le=100000, examples=[190], description="Our cost per veg guest")
    veg_items: list[str] = Field(default_factory=list, max_length=40)
    # Non-veg guests
    nonveg_guests: int = Field(default=0, ge=0, le=10000, examples=[120])
    nonveg_package: str = Field(default="", max_length=60, examples=["Non-Veg Classic"])
    nonveg_price: int = Field(default=0, ge=0, le=100000, examples=[450])
    nonveg_cost: int = Field(default=0, ge=0, le=100000, examples=[260])
    nonveg_items: list[str] = Field(default_factory=list, max_length=40)
    # Extras
    extra_charges: int = Field(default=0, ge=0, le=10_000_000, examples=[5000],
                               description="Customer pays extra: decoration, hall, music... (Rs.)")
    extra_cost: int = Field(default=0, ge=0, le=10_000_000, examples=[3000],
                            description="Our extra costs: decoration, hall, music... (Rs.)")


EVENT_FIELDS = ("title", "event_type", "status", "date", "time", "description", "customer", "phone",
                "is_public", "veg_guests", "veg_package", "veg_price", "veg_cost", "veg_items",
                "nonveg_guests", "nonveg_package", "nonveg_price", "nonveg_cost", "nonveg_items",
                "extra_charges", "extra_cost", "guests")


def _event_values(body: EventIn) -> tuple:
    dishes = {r["dish"]: bool(r["is_veg"]) for r in get_menu_full()}
    unknown = [d for d in body.veg_items + body.nonveg_items if d not in dishes]
    if unknown:
        raise HTTPException(status_code=400, detail="Not on the menu: " + ", ".join(unknown))
    not_veg = [d for d in body.veg_items if not dishes[d]]
    if not_veg:
        raise HTTPException(status_code=400, detail="These are not veg, so they cannot be in the veg menu: "
                                                     + ", ".join(not_veg))
    d = body.model_dump()
    d["date"] = body.date.isoformat()
    d["is_public"] = int(body.is_public)
    d["veg_items"] = json.dumps(list(dict.fromkeys(body.veg_items)))
    d["nonveg_items"] = json.dumps(list(dict.fromkeys(body.nonveg_items)))
    d["guests"] = body.veg_guests + body.nonveg_guests
    return tuple(d[f].strip() if isinstance(d[f], str) else d[f] for f in EVENT_FIELDS)


@api.get("/events", tags=["Customer"])
def upcoming_events():
    """Upcoming PUBLIC events (for customers) - only name, date, time and description."""
    return get_public_events()


@api.get("/packages", tags=["Customer"])
def buffet_packages():
    """Buffet packages for parties (for customers) - prices per head and dishes, no costs."""
    return [{k: p[k] for k in ("name", "food_type", "price_per_head", "items", "description")}
            for p in get_packages()]


@api.get("/staff/events", tags=["Staff - Events"])
def all_events(staff: dict = Depends(current_staff)):
    """Every event, including past ones, with money totals."""
    return get_events()


@api.get("/staff/events/options", tags=["Staff - Events"])
def event_options(staff: dict = Depends(current_staff)):
    return {"types": EVENT_TYPES, "statuses": EVENT_STATUSES, "payment_methods": PAYMENT_METHODS}


@api.get("/staff/events/today", tags=["Staff - Events"])
def events_today(staff: dict = Depends(current_staff)):
    """Today's confirmed events: guests (veg / non-veg) and money still to collect."""
    today = dt.date.today().isoformat()
    rows = [e for e in get_events() if e["date"] == today and e["status"] in ("Confirmed", "Completed")]
    return {"date": today, "events": len(rows),
            "total_plates": sum(r["guests"] for r in rows),
            "veg_guests": sum(r["veg_guests"] for r in rows),
            "nonveg_guests": sum(r["nonveg_guests"] for r in rows),
            "total_bill": sum(r["total_bill"] for r in rows),
            "balance": sum(r["balance"] for r in rows), "list": rows}


@api.get("/staff/events/{event_id}", tags=["Staff - Events"])
def event_details(event_id: int, staff: dict = Depends(current_staff)):
    """One event with its payments and the kitchen plan (how much to cook)."""
    e = get_event(event_id)
    if not e:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"event": e, "payments": get_event_payments(event_id), "kitchen": kitchen_plan(e)}


@api.post("/staff/events", tags=["Staff - Events"])
def add_event(body: EventIn, staff: dict = Depends(current_staff)):
    with db() as conn:
        cur = conn.execute(
            f"INSERT INTO events ({', '.join(EVENT_FIELDS)}) VALUES ({', '.join('?' * len(EVENT_FIELDS))})",
            _event_values(body),
        )
    return {"message": f"Event '{body.title.strip()}' added", "id": cur.lastrowid}


@api.put("/staff/events/{event_id}", tags=["Staff - Events"])
def edit_event(event_id: int, body: EventIn, staff: dict = Depends(current_staff)):
    """Change every detail of an event."""
    with db() as conn:
        changed = conn.execute(
            f"UPDATE events SET {', '.join(f + ' = ?' for f in EVENT_FIELDS)} WHERE id = ?",
            (*_event_values(body), event_id),
        ).rowcount
    if not changed:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"message": f"Event '{body.title.strip()}' updated"}


class EventStatus(BaseModel):
    status: Literal["Enquiry", "Confirmed", "Completed", "Cancelled"]


@api.patch("/staff/events/{event_id}/status", tags=["Staff - Events"])
def set_event_status(event_id: int, body: EventStatus, staff: dict = Depends(current_staff)):
    with db() as conn:
        if not conn.execute("UPDATE events SET status = ? WHERE id = ?", (body.status, event_id)).rowcount:
            raise HTTPException(status_code=404, detail="Event not found")
    return {"id": event_id, "status": body.status}


@api.delete("/staff/events/{event_id}", tags=["Staff - Events"])
def delete_event(event_id: int, staff: dict = Depends(current_staff)):
    with db() as conn:
        if not conn.execute("DELETE FROM events WHERE id = ?", (event_id,)).rowcount:
            raise HTTPException(status_code=404, detail="Event not found")
        conn.execute("DELETE FROM event_payments WHERE event_id = ?", (event_id,))
    return {"message": "Event deleted"}


# ---------- Event payments (advance and later payments) ----------
PAYMENT_METHODS = ["Cash", "UPI", "Card", "Bank transfer"]


class PaymentIn(BaseModel):
    amount: int = Field(gt=0, le=10_000_000, examples=[10000], description="Rs.")
    method: Literal["Cash", "UPI", "Card", "Bank transfer"] = "Cash"
    date: Optional[dt.date] = Field(default=None, description="Empty = today")
    note: str = Field(default="", max_length=100, examples=["Advance"])


@api.post("/staff/events/{event_id}/payments", tags=["Staff - Events"])
def add_event_payment(event_id: int, body: PaymentIn, staff: dict = Depends(current_staff)):
    e = get_event(event_id)
    if not e:
        raise HTTPException(status_code=404, detail="Event not found")
    if e["total_bill"] and body.amount > e["balance"]:     # no bill yet (enquiry) = any advance is fine
        raise HTTPException(status_code=400, detail=f"That is more than the balance (Rs. {e['balance']}).")
    with db() as conn:
        conn.execute("INSERT INTO event_payments (event_id, date, amount, method, note, created_by) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (event_id, (body.date or dt.date.today()).isoformat(), body.amount, body.method,
                      body.note.strip(), staff["username"]))
    return {"message": f"Rs. {body.amount} received by {body.method}",
            "balance": e["balance"] - body.amount}


@api.delete("/staff/events/payments/{payment_id}", tags=["Staff - Events"])
def delete_event_payment(payment_id: int, staff: dict = Depends(admin_only)):
    with db() as conn:
        if not conn.execute("DELETE FROM event_payments WHERE id = ?", (payment_id,)).rowcount:
            raise HTTPException(status_code=404, detail="Payment not found")
    return {"message": "Payment deleted"}


# ---------- Buffet packages ----------
class PackageIn(BaseModel):
    name: str = Field(min_length=1, max_length=60, examples=["Veg Classic"])
    food_type: Literal["Veg", "Non-veg"]
    price_per_head: int = Field(ge=0, le=100000, examples=[350])
    cost_per_head: int = Field(ge=0, le=100000, examples=[190])
    items: list[str] = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=200)


def _package_values(body: PackageIn) -> tuple:
    dishes = {r["dish"]: bool(r["is_veg"]) for r in get_menu_full()}
    unknown = [d for d in body.items if d not in dishes]
    if unknown:
        raise HTTPException(status_code=400, detail="Not on the menu: " + ", ".join(unknown))
    if body.food_type == "Veg":
        not_veg = [d for d in body.items if not dishes[d]]
        if not_veg:
            raise HTTPException(status_code=400, detail="A veg package cannot have: " + ", ".join(not_veg))
    return (body.name.strip(), body.food_type, body.price_per_head, body.cost_per_head,
            json.dumps(list(dict.fromkeys(body.items))), body.description.strip())


@api.get("/staff/packages", tags=["Staff - Events"])
def list_packages(staff: dict = Depends(current_staff)):
    return get_packages()


@api.post("/staff/packages", tags=["Staff - Events"])
def add_package(body: PackageIn, staff: dict = Depends(admin_only)):
    with db() as conn:
        if conn.execute("SELECT 1 FROM event_packages WHERE name = ?", (body.name.strip(),)).fetchone():
            raise HTTPException(status_code=400, detail="A package with this name already exists")
        conn.execute("INSERT INTO event_packages (name, food_type, price_per_head, cost_per_head, items, "
                     "description) VALUES (?, ?, ?, ?, ?, ?)", _package_values(body))
    return {"message": f"Package '{body.name.strip()}' added"}


@api.put("/staff/packages/{package_id}", tags=["Staff - Events"])
def edit_package(package_id: int, body: PackageIn, staff: dict = Depends(admin_only)):
    with db() as conn:
        clash = conn.execute("SELECT 1 FROM event_packages WHERE name = ? AND id != ?",
                             (body.name.strip(), package_id)).fetchone()
        if clash:
            raise HTTPException(status_code=400, detail="Another package already has this name")
        if not conn.execute("UPDATE event_packages SET name = ?, food_type = ?, price_per_head = ?, "
                            "cost_per_head = ?, items = ?, description = ? WHERE id = ?",
                            (*_package_values(body), package_id)).rowcount:
            raise HTTPException(status_code=404, detail="Package not found")
    return {"message": f"Package '{body.name.strip()}' updated"}


@api.delete("/staff/packages/{package_id}", tags=["Staff - Events"])
def delete_package(package_id: int, staff: dict = Depends(admin_only)):
    with db() as conn:
        if not conn.execute("DELETE FROM event_packages WHERE id = ?", (package_id,)).rowcount:
            raise HTTPException(status_code=404, detail="Package not found")
    return {"message": "Package deleted"}


# =========================================================
# ADMIN: MONEY (spending, labour cost, profit)
# =========================================================
EXPENSE_CATEGORIES = ["Ingredients", "Gas and electricity", "Rent", "Maintenance",
                      "Extra labour", "Other running cost", "Investment (one-time)"]
INVESTMENT = "Investment (one-time)"


class ExpenseIn(BaseModel):
    date: dt.date = Field(examples=["2026-10-12"])
    category: Literal["Ingredients", "Gas and electricity", "Rent", "Maintenance",
                      "Extra labour", "Other running cost", "Investment (one-time)"]
    description: str = Field(default="", max_length=120, examples=["Vegetables from market"])
    amount: int = Field(gt=0, le=10_000_000, examples=[2500], description="Rs.")


@api.get("/staff/expenses", tags=["Admin - Money"])
def list_expenses(day: Optional[dt.date] = None, staff: dict = Depends(admin_only)):
    d = (day or dt.date.today()).isoformat()
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM expenses WHERE date = ? ORDER BY id DESC", (d,))]


@api.post("/staff/expenses", tags=["Admin - Money"])
def add_expense(body: ExpenseIn, staff: dict = Depends(admin_only)):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (date, category, description, amount, created_by) VALUES (?, ?, ?, ?, ?)",
            (body.date.isoformat(), body.category, body.description.strip(), body.amount, staff["username"]),
        )
    return {"message": f"Rs. {body.amount} added under {body.category}", "id": cur.lastrowid}


@api.delete("/staff/expenses/{expense_id}", tags=["Admin - Money"])
def delete_expense(expense_id: int, staff: dict = Depends(admin_only)):
    with db() as conn:
        if not conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,)).rowcount:
            raise HTTPException(status_code=404, detail="Expense not found")
    return {"message": "Expense deleted"}


@api.get("/staff/finance", tags=["Admin - Money"])
def finance_report(day: Optional[dt.date] = None, staff: dict = Depends(admin_only)):
    """Sales, running costs, labour and profit for one day, plus the 7 days up to it."""
    day = day or dt.date.today()
    sales = sales_report(day, staff)                       # reuses the Sales tab numbers
    week = [w["date"] for w in sales["last_7_days"]]
    with db() as conn:
        labour = conn.execute("SELECT COALESCE(SUM(daily_wage), 0) FROM staff").fetchone()[0]
        paid_staff = conn.execute("SELECT COUNT(*) FROM staff WHERE daily_wage > 0").fetchone()[0]
        rows = conn.execute("SELECT date, category, amount FROM expenses WHERE date BETWEEN ? AND ?",
                            (week[0], week[-1])).fetchall()
        invested_total = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE category = ?",
                                      (INVESTMENT,)).fetchone()[0]

    costs = {w: 0 for w in week}
    invested = {w: 0 for w in week}
    by_category = {c: 0 for c in EXPENSE_CATEGORIES if c != INVESTMENT}
    for r in rows:
        if r["category"] == INVESTMENT:
            invested[r["date"]] += r["amount"]
        else:
            costs[r["date"]] += r["amount"]
            if r["date"] == week[-1]:
                by_category[r["category"]] = by_category.get(r["category"], 0) + r["amount"]

    days = []
    for w in sales["last_7_days"]:
        profit = w["collected"] - costs[w["date"]] - labour
        days.append({"date": w["date"], "sales": w["collected"], "running_costs": costs[w["date"]],
                     "labour": labour, "profit": profit, "invested": invested[w["date"]]})

    today = days[-1]
    s_amt = today["sales"]
    return {
        "date": day.isoformat(),
        "sales": s_amt,
        "running_costs": today["running_costs"],
        "labour": labour, "paid_staff": paid_staff,
        "profit": today["profit"],
        "invested_today": today["invested"], "invested_total": invested_total,
        "labour_percent": round(labour * 100 / s_amt) if s_amt else None,
        "food_cost_percent": round(by_category["Ingredients"] * 100 / s_amt) if s_amt else None,
        "costs_by_category": by_category,
        "last_7_days": days,
        "categories": EXPENSE_CATEGORIES,
    }
