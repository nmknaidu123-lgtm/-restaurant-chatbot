"""
auth.py - Staff login / logout with tokens (saved in the database).

First-time accounts (change the passwords on the Admin page!):
  admin  / admin123   (role: admin)
  staff1 / staff123   (role: staff)
"""

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.database import db, hash_password

router = APIRouter(prefix="/staff", tags=["Staff login"])

TOKEN_HOURS = 8
bearer = HTTPBearer(auto_error=False)


def current_staff(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    """Checks the token on every staff request."""
    if creds is None:
        raise HTTPException(status_code=401, detail="Please log in first")
    with db() as conn:
        row = conn.execute("SELECT * FROM tokens WHERE token = ?", (creds.credentials,)).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Invalid token or logged out")
        if datetime.fromisoformat(row["expires"]) < datetime.now():
            conn.execute("DELETE FROM tokens WHERE token = ?", (creds.credentials,))
            raise HTTPException(status_code=401, detail="Session expired, please log in again")
    return {"username": row["username"], "role": row["role"],
            "expires": row["expires"], "token": row["token"]}


def admin_only(staff: dict = Depends(current_staff)) -> dict:
    if staff["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access only")
    return staff


class LoginRequest(BaseModel):
    username: str = Field(examples=["admin"])
    password: str = Field(examples=["admin123"])


@router.post("/login")
def login(req: LoginRequest):
    with db() as conn:
        # Case-insensitive and trimmed, so phones that type "Admin " still match "admin"
        user = conn.execute("SELECT * FROM staff WHERE username = ? COLLATE NOCASE",
                            (req.username.strip(),)).fetchone()
        if not user or not secrets.compare_digest(
            hash_password(req.password, user["salt"]), user["password_hash"]
        ):
            raise HTTPException(status_code=401, detail="Wrong username or password")
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(hours=TOKEN_HOURS)).isoformat(timespec="seconds")
        conn.execute("INSERT INTO tokens VALUES (?, ?, ?, ?)",
                     (token, user["username"], user["role"], expires))
    return {"access_token": token, "token_type": "bearer", "username": user["username"],
            "role": user["role"], "expires_in_hours": TOKEN_HOURS}


@router.post("/logout")
def logout(staff: dict = Depends(current_staff)):
    with db() as conn:
        conn.execute("DELETE FROM tokens WHERE token = ?", (staff["token"],))
    return {"message": f"{staff['username']} logged out"}


@router.get("/me")
def me(staff: dict = Depends(current_staff)):
    return {"username": staff["username"], "role": staff["role"], "expires": staff["expires"]}
