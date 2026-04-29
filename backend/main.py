"""
Backend FastAPI – Hệ thống quản lý phòng trọ
Chạy: uvicorn main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import sys
import os

# Thêm frontend vào sys.path để dùng chung models, db, services
_PROJECT_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_PROJECT_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routers import auth, rooms, tenants, contracts, payments, users, notifications, dashboard, price, audit, ai

app = FastAPI(
    title="Tro Gia API",
    description="REST API cho hệ thống quản lý phòng trọ – Admin & User",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS – cho phép Streamlit frontend và bất kỳ dev client nào gọi API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (ảnh phòng)
_STATIC_DIR = os.path.join(_PROJECT_DIR, "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth.router,          prefix="/api/auth",               tags=["Auth"])
app.include_router(dashboard.router,     prefix="/api/dashboard",          tags=["Dashboard"])
app.include_router(rooms.router,         prefix="/api/rooms",              tags=["Rooms"])
app.include_router(tenants.router,       prefix="/api/tenants",            tags=["Tenants"])
app.include_router(contracts.router,     prefix="/api/contracts",          tags=["Contracts"])
app.include_router(payments.router,      prefix="/api/payments",           tags=["Payments"])
app.include_router(users.router,         prefix="/api/users",              tags=["Users"])
app.include_router(notifications.router, prefix="/api/notifications",      tags=["Notifications"])
app.include_router(price.router,         prefix="/api/price-suggestions",  tags=["Price Suggestions"])
app.include_router(audit.router,         prefix="/api/audit-logs",         tags=["Audit Logs"])
app.include_router(ai.router,            prefix="/api/ai",                 tags=["AI"])


@app.get("/", tags=["Health"])
def health():
    return {"status": "ok", "message": "Tro Gia API đang chạy"}
