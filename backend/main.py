"""
Backend FastAPI - He thong quan ly phong tro
Chay: uvicorn backend.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

_BACKEND_DIR = os.path.abspath(os.path.dirname(__file__))
_PROJECT_DIR = os.path.abspath(os.path.join(_BACKEND_DIR, "..", "frontend"))

# Ho tro chay tu root (`uvicorn backend.main:app`) va tu thu muc backend (`uvicorn main:app`).
for _path in (_BACKEND_DIR, _PROJECT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from routers import ai, audit, auth, contracts, dashboard, notifications, payments, price, rooms, tenants, users

app = FastAPI(
    title="Tro Gia API",
    description="REST API cho he thong quan ly phong tro - Admin & User",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC_DIR = os.path.join(_PROJECT_DIR, "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(rooms.router, prefix="/api/rooms", tags=["Rooms"])
app.include_router(tenants.router, prefix="/api/tenants", tags=["Tenants"])
app.include_router(contracts.router, prefix="/api/contracts", tags=["Contracts"])
app.include_router(payments.router, prefix="/api/payments", tags=["Payments"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(price.router, prefix="/api/price-suggestions", tags=["Price Suggestions"])
app.include_router(audit.router, prefix="/api/audit-logs", tags=["Audit Logs"])
app.include_router(ai.router, prefix="/api/ai", tags=["AI"])


@app.get("/", tags=["Health"])
def health():
    return {"status": "ok", "message": "Tro Gia API dang chay"}
