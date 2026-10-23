# Restaurant Chatbot

A restaurant assistant built with **FastAPI**, **LangGraph** and **Google Gemini**.
Customers chat to order food, book tables and ask about events; staff manage the menu, orders,
bookings, events, discounts and expenses from an admin page, with an AI manager assistant.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
Create a `.env` file:
```
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-3.5-flash-lite
```
Start it:
```powershell
uvicorn app.main:api --reload
```
- Admin page: http://127.0.0.1:8000/admin
- API docs: http://127.0.0.1:8000/docs

## Deploy on Render (free)

1. New → Web Service → connect this repo. Language: **Python 3**. Instance type: **Free**.
2. Build command: `pip install -r requirements.txt`
3. Start command: `uvicorn app.main:api --host 0.0.0.0 --port $PORT`
4. Environment variables:

| Key | Value |
|---|---|
| `GEMINI_API_KEY` | your Gemini key |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` |
| `ADMIN_PASSWORD` | a strong password for the `admin` login |
| `STAFF_PASSWORD` | a strong password for the `staff1` login |

**Note:** data is stored in SQLite (`restaurant.db`). On Render's free plan the disk is temporary,
so the demo resets to the default menu and accounts whenever the service restarts.
