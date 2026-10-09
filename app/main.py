"""
main.py - The FastAPI backend (all endpoints).

Run:    uvicorn app.main:api --reload
Admin:  http://127.0.0.1:8000/admin   (manage everything in one place)
API:    http://127.0.0.1:8000/docs
"""

import json
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.database import db, init_db, new_password_record
from app.data import (get_setting, set_setting, get_menu_by_category, get_menu_full,
                      get_order_cart, save_order, get_order_status, save_booking, get_booking)
from app.graph import bot, session_state, clear_session
from app.discounts import generate_code, list_codes, delete_code, apply_code, calculate_bill
from app.auth import router as auth_router, current_staff, admin_only

init_db()      # creates restaurant.db with default data the first time

api = FastAPI(title="Restaurant Chatbot API")
api.include_router(auth_router)          # /staff/login, /staff/logout, /staff/me

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
    inputs = {"user_input": req.message, "session_id": req.session_id}
    # After a server restart the chatbot's memory is empty - reload the saved cart
    if not session_state(req.session_id).get("cart"):
        saved_cart = get_order_cart(req.session_id)
        if saved_cart:
            inputs["cart"] = saved_cart

    result = bot.invoke(inputs, config=config)

    if result.get("cart"):
        save_order(req.session_id, result["cart"])
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
                       "updated_at": r["updated_at"], **calculate_bill(r["session_id"], cart)})
    return {"count": len(orders), "orders": orders}


@api.put("/staff/orders/{session_id}/status", tags=["Staff - Orders"])
def update_status(session_id: str, body: StatusUpdate, staff: dict = Depends(current_staff)):
    with db() as conn:
        changed = conn.execute("UPDATE orders SET status = ? WHERE session_id = ?",
                               (body.status, session_id)).rowcount
    if not changed:
        raise HTTPException(status_code=404, detail="No order for this session")
    return {"session_id": session_id, "status": body.status, "updated_by": staff["username"]}


@api.get("/staff/bookings", tags=["Staff - Orders"])
def all_bookings(staff: dict = Depends(current_staff)):
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM bookings ORDER BY date, time")]
    return {"count": len(rows), "bookings": rows}


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
        conn.execute(
            "INSERT INTO menu (dish, price, category, available) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(dish) DO UPDATE SET price = excluded.price, "
            "category = excluded.category, available = excluded.available",
            (dish, item.price, item.category.strip(), int(item.available)),
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


class StaffUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=6, max_length=100)
    role: Optional[Literal["staff", "admin"]] = None


@api.get("/staff/users", tags=["Admin - Staff accounts"])
def list_staff(staff: dict = Depends(admin_only)):
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT username, role FROM staff ORDER BY username")]


@api.post("/staff/users", tags=["Admin - Staff accounts"])
def add_staff(body: NewStaff, staff: dict = Depends(admin_only)):
    salt, pw_hash = new_password_record(body.password)
    with db() as conn:
        if conn.execute("SELECT 1 FROM staff WHERE username = ?", (body.username,)).fetchone():
            raise HTTPException(status_code=400, detail="Username already exists")
        conn.execute("INSERT INTO staff VALUES (?, ?, ?, ?)",
                     (body.username, salt, pw_hash, body.role))
    return {"message": f"{body.username} added as {body.role}"}


@api.put("/staff/users/{username}", tags=["Admin - Staff accounts"])
def update_staff(username: str, body: StaffUpdate, staff: dict = Depends(admin_only)):
    if body.password is None and body.role is None:
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
        # Log this user out everywhere so the change takes effect at once
        if username != staff["username"]:
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
