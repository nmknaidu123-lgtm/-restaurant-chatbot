"""
graph.py - The LangGraph chatbot (the "brain").
One customer message in -> one reply out.
MemorySaver remembers each customer's cart and booking by session_id.
"""

import os
from typing import TypedDict, Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.data import get_menu, get_menu_by_category, get_setting, get_public_events, get_packages
from app.discounts import calculate_bill

load_dotenv()   # reads GEMINI_API_KEY from the .env file

# ---------------- The LLM ----------------
llm = ChatGoogleGenerativeAI(
    # Set GEMINI_MODEL in .env / Render to change it (Flash Lite has a bigger free limit)
    model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
    api_key=os.getenv("GEMINI_API_KEY"),
)


def text_of(message) -> str:
    """Gemini sometimes returns a list of parts; turn it into plain text."""
    content = message.content
    if isinstance(content, list):
        content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return content


# ---------------- The STATE ----------------
class RestaurantState(TypedDict, total=False):
    session_id: str
    user_input: str
    intent: str
    reply: str
    cart: dict
    booking: dict
    awaiting_booking: bool
    new_notes: dict          # special requests from THIS message only (saved by main.py)


class Intent(BaseModel):
    intent: Literal["menu", "order", "booking", "question", "bill", "events"]


class OrderItem(BaseModel):
    name: str = Field(description="Exact dish name from the menu")
    quantity: int
    request: Optional[str] = Field(
        default=None,
        description="Special request for THIS dish only, short: spice level (mild, medium, spicy, "
                    "extra spicy), 'for baby/kids', 'no onion', etc. Empty if none.")


class Order(BaseModel):
    items: list[OrderItem]


class BookingDetails(BaseModel):
    name: Optional[str] = None
    people: Optional[int] = None
    date: Optional[str] = None
    time: Optional[str] = None


# ---------------- The NODES ----------------
def classify(state: RestaurantState) -> RestaurantState:
    if state.get("awaiting_booking"):       # still collecting booking details
        return {"intent": "booking"}
    result = llm.with_structured_output(Intent).invoke(
        "Classify this restaurant customer message:\n"
        "menu = wants the menu or prices\n"
        "order = wants to order food\n"
        "booking = wants to reserve a table\n"
        "question = any other question about the restaurant\n"
        "bill = wants the bill, total, or is finishing\n"
        "events = asks about events, parties, live music, festivals or what's happening\n\n"
        f"Message: {state['user_input']}"
    )
    return {"intent": result.intent}


def show_menu(state: RestaurantState) -> RestaurantState:
    lines = []
    for category, dishes in get_menu_by_category().items():
        lines.append(f"\n{category}:")
        lines += [f"  {dish} - Rs. {price}" for dish, price in dishes.items()]
    name = get_setting("restaurant_name")
    return {"reply": f"Here is the {name} menu:" + "\n".join(lines)}


def take_order(state: RestaurantState) -> RestaurantState:
    menu = get_menu()
    order = llm.with_structured_output(Order).invoke(
        f"Menu: {list(menu.keys())}\n"
        "Extract dishes and quantities. Use exact menu names. Ignore items not on the menu.\n"
        "If the customer asks for something special for a dish (spice level, for a baby or child, "
        "no onion, less oil...), put it in that dish's request.\n"
        f"Customer: {state['user_input']}"
    )
    cart = dict(state.get("cart") or {})
    added, notes = [], {}
    for item in order.items:
        if item.name in menu and item.quantity > 0:
            cart[item.name] = cart.get(item.name, 0) + item.quantity
            line = f"{item.quantity} x {item.name}"
            request = (item.request or "").strip()[:100]
            if request:
                notes.setdefault(item.name, []).append(f"{item.quantity} x {request}")
                line += f" ({request})"
            added.append(line)
    if not added:
        return {"reply": "Sorry, I couldn't find those dishes on our menu. Ask for the menu to see it."}
    total = calculate_bill(state.get("session_id", ""), cart)["total"]
    return {"cart": cart, "new_notes": notes,
            "reply": f"Added: {', '.join(added)}. Current total: Rs. {total}"}


def book_table(state: RestaurantState) -> RestaurantState:
    found = llm.with_structured_output(BookingDetails).invoke(
        "Extract table booking details (name, number of people, date, time) from this "
        f"message. Leave unknown fields empty.\nMessage: {state['user_input']}"
    )
    booking = dict(state.get("booking") or {})
    for key, value in found.model_dump().items():
        if value:
            booking[key] = value
    missing = [k for k in ["name", "people", "date", "time"] if not booking.get(k)]
    if missing:
        return {"booking": booking, "awaiting_booking": True,
                "reply": "Sure! Please tell me your " + ", ".join(missing) + "."}
    return {"booking": booking, "awaiting_booking": False,
            "reply": (f"Table booked for {booking['name']}, {booking['people']} people "
                      f"on {booking['date']} at {booking['time']}. See you soon!")}


def _packages_for_customers() -> list:
    return [{"name": p["name"], "type": p["food_type"], "price_per_head": p["price_per_head"],
             "dishes": p["items"]} for p in get_packages()]


def answer_question(state: RestaurantState) -> RestaurantState:
    reply = llm.invoke(
        f"You are a friendly waiter at {get_setting('restaurant_name')}. Answer in 1-3 short "
        "sentences using ONLY this information. If the answer is not here, say you are not sure.\n"
        f"{get_setting('restaurant_info')}\nMenu (dish: price in Rs.): {get_menu()}\n"
        f"Party / event buffet packages (price per head in Rs.): {_packages_for_customers()}\n"
        "For a party booking, ask the customer to call the restaurant.\n\n"
        f"Customer: {state['user_input']}"
    )
    return {"reply": text_of(reply)}


def show_bill(state: RestaurantState) -> RestaurantState:
    cart = state.get("cart") or {}
    if not cart:
        return {"reply": "You haven't ordered anything yet."}
    prices = get_menu(include_unavailable=True)
    lines = [f"{q} x {d} = Rs. {prices.get(d, 0) * q}" for d, q in cart.items()]
    bill = calculate_bill(state.get("session_id", ""), cart)
    text = "Your bill:\n" + "\n".join(lines) + f"\nSubtotal: Rs. {bill['subtotal']}"
    if bill["discount"]:
        text += f"\nDiscount ({bill['discount_code']}, {bill['discount_percent']}%): -Rs. {bill['discount']}"
    return {"reply": text + f"\nTOTAL: Rs. {bill['total']}\nThank you!"}


def show_events(state: RestaurantState) -> RestaurantState:
    events = get_public_events()          # only public events - never private parties
    if not events:
        return {"reply": "There are no upcoming events right now. Please check again soon!"}
    lines = []
    for e in events:
        when = e["date"] + (f" at {e['time']}" if e.get("time") else "")
        line = f"- {e['title']} ({when})"
        if e.get("description"):
            line += f": {e['description']}"
        lines.append(line)
    return {"reply": "Upcoming events:\n" + "\n".join(lines)}


def route(state: RestaurantState) -> str:
    return state["intent"]


# ---------------- Build the GRAPH ----------------
graph = StateGraph(RestaurantState)
graph.add_node("classify", classify)
graph.add_node("show_menu", show_menu)
graph.add_node("take_order", take_order)
graph.add_node("book_table", book_table)
graph.add_node("answer_question", answer_question)
graph.add_node("show_bill", show_bill)
graph.add_node("show_events", show_events)

graph.add_edge(START, "classify")
graph.add_conditional_edges("classify", route, {
    "menu": "show_menu",
    "order": "take_order",
    "booking": "book_table",
    "question": "answer_question",
    "bill": "show_bill",
    "events": "show_events",
})
for node in ["show_menu", "take_order", "book_table", "answer_question", "show_bill", "show_events"]:
    graph.add_edge(node, END)

bot = graph.compile(checkpointer=MemorySaver())


def session_state(session_id: str) -> dict:
    """Read the saved state (cart, booking...) for one customer."""
    return bot.get_state({"configurable": {"thread_id": session_id}}).values or {}


def clear_session(session_id: str) -> None:
    """Forget one customer's chat history (cart, booking, conversation state)."""
    try:
        bot.checkpointer.delete_thread(session_id)
    except (AttributeError, NotImplementedError):
        pass
