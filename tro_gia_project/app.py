from __future__ import annotations

import base64
import html
import hashlib
import io
import json
import os
import re
import time
import uuid
import urllib.parse
try:
    import qrcode  # type: ignore
    from qrcode.image.pil import PilImage  # type: ignore
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False
try:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore
    from google.genai.errors import APIError  # type: ignore
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    func,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, joinedload, mapped_column, relationship, sessionmaker
from services.audit_service import write_audit_log
from services.notification_service import (
    create_notification,
    get_user_notifications,
    get_unread_count,
    mark_as_read,
    mark_all_as_read,
    notify_payment_created,
    notify_payment_paid,
    notify_admin_new_payment_pending,
    send_admin_to_user_message,
    send_user_to_admin_message,
)
from services.ai_automation_service import AIAutomationService, AIChatAgent
from services.momo_service import (
    create_momo_payment_link,
    check_momo_payment_status,
    process_momo_callback,
    get_momo_service_from_env,
    momo_payment_store,
)

def serialize_model(model: Any) -> dict[str, Any]:
    if not model:
        return {}
    res = {}
    for c in model.__table__.columns:
        val = getattr(model, c.name)
        if hasattr(val, "isoformat"):
            res[c.name] = val.isoformat()
        elif isinstance(val, Decimal):
            res[c.name] = float(val)
        else:
            res[c.name] = val
    return res

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "Hệ thống quản lý phòng trọ và gợi ý giá thuê")
APP_NAME = APP_NAME.replace("quản lý giá phòng trọ", "quản lý phòng trọ")
DATABASE_URL = os.getenv("DATABASE_URL", "mysql+pymysql://root:@127.0.0.1:3306/boarding_house")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123")
ADMIN_FULL_NAME = os.getenv("ADMIN_FULL_NAME", "Chủ trọ mặc định")

# MoMo payment config (set in .env or use defaults for demo)
MOMO_PHONE = os.getenv("MOMO_PHONE", "0909000000")   # Số điện thoại MoMo chủ nhà
MOMO_NAME = os.getenv("MOMO_NAME", "Chủ Trọ")        # Tên hiển thị trên MoMo

# VietQR bank transfer config (optional)
BANK_ACCOUNT_NO = os.getenv("BANK_ACCOUNT_NO", "")   # Số tài khoản ngân hàng
BANK_NAME = os.getenv("BANK_NAME", "")               # Tên ngân hàng (VD: VCB, TCB)
BANK_ACCOUNT_NAME = os.getenv("BANK_ACCOUNT_NAME", MOMO_NAME)  # Tên chủ tài khoản

GEMINI_API_KEY = ""  # API key must be entered by user - old key was leaked

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

st.set_page_config(
    page_title=APP_NAME,
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": None
    }
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    rooms: Mapped[list[Room]] = relationship(back_populates="owner")
    tenant_profile: Mapped[Tenant | None] = relationship(back_populates="user", uselist=False)
    audit_logs: Mapped[list[AuditLog]] = relationship(back_populates="actor")
    sent_notifications: Mapped[list[Notification]] = relationship("Notification", foreign_keys="Notification.sender_id", back_populates="sender")
    received_notifications: Mapped[list[Notification]] = relationship("Notification", foreign_keys="Notification.recipient_id", back_populates="recipient")


class Room(Base):
    __tablename__ = "rooms"

    room_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    room_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    area_m2: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    khu_vuc: Mapped[str] = mapped_column(String(100), nullable=False)
    tang: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    current_rent: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="available", nullable=False)
    has_aircon: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_fridge: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_water_heater: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_balcony: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_elevator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="rooms")
    contracts: Mapped[list[Contract]] = relationship(back_populates="room")
    price_suggestions: Mapped[list[PriceSuggestion]] = relationship(back_populates="room", cascade="all, delete-orphan")
    images: Mapped[list[RoomImage]] = relationship(back_populates="room", cascade="all, delete-orphan")


class RoomImage(Base):
    __tablename__ = "room_images"

    image_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.room_id"), nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    room: Mapped[Room] = relationship(back_populates="images")


class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))
    id_number: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    user: Mapped[User | None] = relationship(back_populates="tenant_profile")
    contracts: Mapped[list[Contract]] = relationship(back_populates="tenant")


class Contract(Base):
    __tablename__ = "contracts"

    contract_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.room_id"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.tenant_id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    rent_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    deposit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    payment_cycle: Mapped[str] = mapped_column(String(20), default="monthly", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    terms: Mapped[str | None] = mapped_column(Text)
    digital_signature: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    room: Mapped[Room] = relationship(back_populates="contracts")
    tenant: Mapped[Tenant] = relationship(back_populates="contracts")
    payments: Mapped[list[Payment]] = relationship(back_populates="contract")


class Payment(Base):
    __tablename__ = "payments"

    payment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.contract_id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    electricity_old: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    electricity_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    water_old: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    water_new: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    electricity_unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    water_unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    service_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    paid_date: Mapped[date | None] = mapped_column(Date)
    method: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="unpaid", nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    contract: Mapped[Contract] = relationship(back_populates="payments")


class PriceSuggestion(Base):
    __tablename__ = "price_suggestions"

    suggestion_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.room_id"), nullable=False)
    suggested_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    based_on_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    algo_version: Mapped[str | None] = mapped_column(String(50))
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    room: Mapped[Room] = relationship(back_populates="price_suggestions")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"))
    entity_name: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    old_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    new_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    actor: Mapped[User | None] = relationship(back_populates="audit_logs")


class Notification(Base):
    __tablename__ = "notifications"

    notification_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"), nullable=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), default="general")
    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    sender: Mapped[User | None] = relationship("User", foreign_keys=[sender_id], back_populates="sent_notifications")
    recipient: Mapped[User] = relationship("User", foreign_keys=[recipient_id], back_populates="received_notifications")


engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

REGION_BENCHMARKS = {
    "trung tâm": {"market_min": 3_200_000, "market_avg": 4_000_000, "market_max": 5_500_000, "reference": "Khảo sát thủ công"},
    "cận trung tâm": {"market_min": 2_600_000, "market_avg": 3_300_000, "market_max": 4_400_000, "reference": "Khảo sát thủ công"},
    "ngoại thành": {"market_min": 2_000_000, "market_avg": 2_700_000, "market_max": 3_700_000, "reference": "Khảo sát thủ công"},
}
AMENITY_LABELS = {
    "has_aircon": "Máy lạnh",
    "has_fridge": "Tủ lạnh",
    "has_water_heater": "Bình nóng lạnh",
    "has_balcony": "Ban công",
    "has_elevator": "Thang máy",
}


ENTITY_LABELS = {
    "users": "Tài khoản",
    "rooms": "Phòng",
    "tenants": "Người thuê",
    "contracts": "Hợp đồng",
    "payments": "Hóa đơn",
    "price_suggestions": "Gợi ý giá",
}
ACTION_LABELS = {
    "insert": "Thêm mới",
    "update": "Cập nhật",
    "delete": "Xóa",
    "payment": "Thanh toán",
}


def room_contact_auto_reply(message: str, room) -> tuple[str, bool]:
    """Returns (reply_text, should_forward_to_admin).
    Handles common tenant questions about a specific room automatically."""
    msg = message.lower()

    if any(k in msg for k in ["xin chào", "chào", "hello", "hi ", "alo"]):
        return (
            f"Xin chào! Tôi là trợ lý tự động của hệ thống. "
            f"Tôi có thể giải đáp các câu hỏi cơ bản về phòng **{room.room_code}**. "
            f"Nếu cần hỗ trợ thêm, admin sẽ phản hồi bạn sớm!",
            False,
        )

    if any(k in msg for k in ["giá", "tiền thuê", "bao nhiêu", "chi phí", "phí thuê", "giá thuê"]):
        amenities = []
        if room.has_aircon: amenities.append("máy lạnh")
        if room.has_fridge: amenities.append("tủ lạnh")
        if room.has_water_heater: amenities.append("bình nóng lạnh")
        if room.has_balcony: amenities.append("ban công")
        if room.has_elevator: amenities.append("thang máy")
        amenity_str = ", ".join(amenities) if amenities else "cơ bản"
        return (
            f"Phòng **{room.room_code}** có giá thuê **{money(room.current_rent)}/tháng**.\n"
            f"- Diện tích: {float(room.area_m2)} m²\n"
            f"- Tầng: {room.tang} | Khu vực: {room.khu_vuc}\n"
            f"- Tiện ích: {amenity_str}",
            False,
        )

    if any(k in msg for k in ["còn trống", "có phòng", "tình trạng", "trạng thái", "available", "đang thuê"]):
        if room.status == "available":
            return (
                f"Phòng **{room.room_code}** hiện **còn trống** và sẵn sàng cho thuê! "
                f"Giá {money(room.current_rent)}/tháng. Liên hệ admin để đặt phòng.",
                False,
            )
        else:
            return (
                f"Phòng **{room.room_code}** hiện **đang có người thuê**. "
                f"Vui lòng liên hệ admin để hỏi về các phòng trống khác.",
                False,
            )

    if any(k in msg for k in ["tiện nghi", "tiện ích", "nội thất", "máy lạnh", "tủ lạnh", "nóng lạnh", "ban công", "thang máy", "wifi"]):
        amenities = []
        if room.has_aircon: amenities.append("❄️ Máy lạnh")
        if room.has_fridge: amenities.append("🧊 Tủ lạnh")
        if room.has_water_heater: amenities.append("🚿 Bình nóng lạnh")
        if room.has_balcony: amenities.append("🌿 Ban công")
        if room.has_elevator: amenities.append("🛗 Thang máy")
        if amenities:
            return (f"Phòng **{room.room_code}** có các tiện ích: {', '.join(amenities)}.", False)
        else:
            return (f"Phòng **{room.room_code}** có trang bị cơ bản. Hỏi admin để biết thêm chi tiết.", False)

    if any(k in msg for k in ["địa chỉ", "vị trí", "đường", "quận", "khu vực", "ở đâu", "chỗ nào"]):
        addr = room.address or "liên hệ admin để biết thêm"
        return (
            f"Phòng **{room.room_code}** nằm tại khu vực **{room.khu_vuc}**, tầng {room.tang}.\n"
            f"Địa chỉ: {addr}.",
            False,
        )

    if any(k in msg for k in ["diện tích", "rộng", "m2", "m²", "mét vuông"]):
        return (f"Phòng **{room.room_code}** có diện tích **{float(room.area_m2)} m²**.", False)

    if any(k in msg for k in ["hợp đồng", "đặt cọc", "cọc", "giấy tờ", "thủ tục", "cmnd", "căn cước", "ký hợp"]):
        return (
            f"Câu hỏi về hợp đồng/thủ tục đã được ghi nhận. "
            f"**Admin sẽ xem xét và phản hồi bạn sớm** qua hệ thống thông báo!",
            True,
        )

    if any(k in msg for k in ["thanh toán", "chuyển khoản", "ngân hàng", "momo", "tiền cọc", "đặt cọc"]):
        return (
            f"Thông tin thanh toán cần xác nhận từ admin. "
            f"**Admin sẽ liên hệ bạn** về phương thức và chi tiết thanh toán!",
            True,
        )

    # Default: forward to admin
    return (
        f"Cảm ơn bạn đã liên hệ! Câu hỏi của bạn đã được ghi nhận và "
        f"**admin sẽ phản hồi sớm nhất có thể** qua hệ thống thông báo.",
        True,
    )


def room_code_text(room: Room | None) -> str:
    if not room:
        return "N/A"
    code = (room.room_code or "").strip()
    return code if code else f"P{room.room_id}"


def room_label(room: Room | None) -> str:
    if not room:
        return "N/A"
    return f"{room_code_text(room)} • {room.khu_vuc} • {room.status}"


def tenant_label(tenant: Tenant | None) -> str:
    if not tenant:
        return "N/A"
    user_part = f" • user: {tenant.user.username}" if getattr(tenant, "user", None) else " • chưa liên kết"
    phone = f" • {tenant.phone}" if tenant.phone else ""
    return f"{tenant.full_name}{phone}{user_part}"


def display_status(st: str) -> str:
    if st == "available": return "Còn trống"
    if st == "occupied": return "Đã thuê"
    return st

def db_status(st: str) -> str:
    if st == "Còn trống": return "available"
    if st == "Đã thuê": return "occupied"
    return st

def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_room_search(room: Room) -> str:
    return " ".join(
        filter(
            None,
            [
                room_code_text(room),
                clean_text(room.khu_vuc),
                clean_text(room.address),
                display_status(room.status),
            ],
        )
    ).lower()


def audit_kv_frame(data: dict[str, Any] | None) -> pd.DataFrame:
    if not data:
        return pd.DataFrame(columns=["Trường", "Giá trị"])
    rows = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            display_value = json.dumps(value, ensure_ascii=False)
        else:
            display_value = "" if value is None else str(value)
        rows.append({"Trường": key, "Giá trị": display_value})
    return pd.DataFrame(rows)


def generate_ai_price_advice(room: Room, suggested_price: Decimal, breakdown: dict[str, Any], current_rent: Decimal | None = None) -> str:
    current = to_decimal(current_rent if current_rent is not None else room.current_rent)
    suggested = to_decimal(suggested_price)
    market_avg = to_decimal(breakdown.get("gia_trung_binh_khu_vuc", 0))
    diff = suggested - current
    abs_diff = abs(diff)
    position = "thấp hơn" if current < suggested else "cao hơn" if current > suggested else "bằng"

    reasons = []
    if breakdown.get("dieu_chinh_dien_tich", 0):
        reasons.append(f"diện tích {float(room.area_m2):.0f}m²")
    amenities = breakdown.get("tien_ich_ap_dung") or []
    if amenities:
        reasons.append("tiện ích: " + ", ".join(amenities))
    if int(room.tang or 1) > 1:
        reasons.append(f"vị trí tầng {room.tang}")

    if abs_diff <= Decimal("200000"):
        decision = "Mức giá hiện tại đã khá sát mức khuyến nghị."
    elif diff > 0:
        decision = f"Nên cân nhắc tăng khoảng {money(abs_diff)} để tiệm cận mặt bằng khu vực."
    else:
        decision = f"Giá hiện tại đang cao hơn khuyến nghị khoảng {money(abs_diff)}; nên cân nhắc giảm hoặc bổ sung tiện ích để thuyết phục khách thuê."

    reason_text = "; ".join(reasons) if reasons else "đặc điểm phòng hiện tại"
    return (
        f"Phân tích AI nội bộ: Giá khuyến nghị cho {room_code_text(room)} tại khu vực {room.khu_vuc} là {money(suggested)}. "
        f"Giá này được so với mức trung bình khu vực {money(market_avg)} và điều chỉnh theo {reason_text}. "
        f"Hiện giá đang {position} mức khuyến nghị. {decision}"
    )


def summarize_audit_data(entity_name: str, data: dict[str, Any] | None) -> str:
    if not data:
        return "Không có"
    if entity_name == "rooms":
        return " | ".join(
            filter(
                None,
                [
                    f"Mã phòng: {data.get('room_code') or f'P{data.get('room_id')}' if data.get('room_id') else ''}",
                    f"Khu vực: {data.get('khu_vuc') or ''}",
                    f"Giá: {money(data.get('current_rent', 0))}" if data.get("current_rent") is not None else "",
                    f"Trạng thái: {data.get('status') or ''}",
                ],
            )
        )
    if entity_name == "tenants":
        return " | ".join(
            filter(
                None,
                [
                    f"Họ tên: {data.get('full_name') or ''}",
                    f"SĐT: {data.get('phone') or ''}",
                    f"Email: {data.get('email') or ''}",
                ],
            )
        )
    if entity_name == "contracts":
        return " | ".join(
            filter(
                None,
                [
                    f"Phòng ID: {data.get('room_id') or ''}",
                    f"Người thuê ID: {data.get('tenant_id') or ''}",
                    f"Giá thuê: {money(data.get('rent_price', 0))}" if data.get("rent_price") is not None else "",
                    f"Trạng thái: {data.get('status') or ''}",
                ],
            )
        )
    if entity_name == "payments":
        return " | ".join(
            filter(
                None,
                [
                    f"Kỳ: {data.get('period') or ''}",
                    f"Số tiền: {money(data.get('amount', 0))}" if data.get("amount") is not None else "",
                    f"Trạng thái: {data.get('status') or ''}",
                ],
            )
        )
    if entity_name == "users":
        return " | ".join(
            filter(
                None,
                [
                    f"Username: {data.get('username') or ''}",
                    f"Email: {data.get('email') or ''}",
                    f"Vai trò: {data.get('role') or ''}",
                ],
            )
        )
    
    items_list = []
    for count, (k, v) in enumerate(data.items()):
        if count >= 4:
            break
        items_list.append(f"{k}: {v}")
    return " | ".join(items_list)


def resolve_tenant_for_user(db: Session, session_user: SessionUser) -> Tenant | None:
    tenant = db.execute(
        select(Tenant)
        .options(joinedload(Tenant.user))
        .where(Tenant.user_id == session_user.user_id)
    ).scalar_one_or_none()
    if tenant:
        return tenant

    account = db.get(User, session_user.user_id)
    if not account:
        return None

    conditions = []
    if account.email:
        conditions.append(Tenant.email == account.email)
    if account.phone:
        conditions.append(Tenant.phone == account.phone)

    if not conditions:
        return None

    tenant = db.execute(
        select(Tenant)
        .options(joinedload(Tenant.user))
        .where(or_(*conditions))
        .order_by(Tenant.tenant_id.desc())
    ).scalar_one_or_none()

    if tenant and tenant.user_id is None:
        old_data = serialize_model(tenant)
        tenant.user_id = session_user.user_id
        db.flush()
        write_audit_log(
            db,
            session_user.user_id,
            "tenants",
            str(tenant.tenant_id),
            "update",
            old_data=old_data,
            new_data=serialize_model(tenant),
        )
    return tenant


@dataclass
class SessionUser:
    user_id: int
    username: str
    full_name: str
    role: str


@contextmanager
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except BaseException as ex:
        # Streamlit dùng RerunException/StopException để làm mới giao diện.
        # Nếu rollback ở đây thì dữ liệu vừa thêm/sửa sẽ biến mất khỏi danh sách.
        if ex.__class__.__name__ in {"RerunException", "StopException"}:
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            raise
        db.rollback()
        raise
    finally:
        db.close()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False

    try:
        return pwd_context.verify(password, password_hash)
    except UnknownHashError:
        # hỗ trợ trường hợp password cũ lưu dạng text
        return password == password_hash


def current_user() -> SessionUser | None:
    raw = st.session_state.get("current_user")
    return SessionUser(**raw) if raw else None


def set_current_user(user: User) -> None:
    st.session_state["current_user"] = {
        "user_id": user.user_id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
    }


def logout() -> None:
    st.session_state.pop("current_user", None)


def to_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value: Any) -> str:
    amount = int(Decimal(str(value)).quantize(Decimal("1")))
    return f"{amount:,.0f} VNĐ"


def serialize_model(obj: Any) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in obj.__table__.columns:
        value = getattr(obj, column.name)
        if isinstance(value, Decimal):
            data[column.name] = float(value)
        elif isinstance(value, (datetime, date)):
            data[column.name] = value.isoformat()
        else:
            data[column.name] = value
    return data


def validate_phone(phone: str) -> bool:
    if not phone:
        return True
    return bool(re.fullmatch(r"0\d{9,10}", phone.strip()))


def validate_email(email: str) -> bool:
    if not email:
        return True
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()))


def validate_password_strength(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Mật khẩu phải có ít nhất 8 ký tự"
    if not re.search(r"[A-Z]", password):
        return False, "Mật khẩu cần có ít nhất 1 chữ in hoa"
    if not re.search(r"[a-z]", password):
        return False, "Mật khẩu cần có ít nhất 1 chữ thường"
    if not re.search(r"\d", password):
        return False, "Mật khẩu cần có ít nhất 1 chữ số"
    return True, "OK"


def validate_contract_dates(start_date: date, end_date: date) -> tuple[bool, str]:
    if end_date < start_date:
        return False, "Ngày kết thúc phải lớn hơn hoặc bằng ngày bắt đầu"
    return True, "OK"


def authenticate(login_value, password):
    user = None

    with SessionLocal() as db:
        user = (
            db.query(User)
            .filter(
                (User.username == login_value) |
                (User.email == login_value) |
                (User.phone == login_value)
            )
            .first()
        )

        if not user:
            return None, None
            
        if user.status != "active":
            return None, "Tài khoản của bạn đã bị vô hiệu hóa hoặc chặn."

        if not verify_password(password, user.password_hash):
            return None, None
        
        # Nếu mật khẩu cũ đang lưu dạng text / hash lạ thì tự nâng cấp sang hash chuẩn
        try:
            identified = pwd_context.identify(user.password_hash)
        except Exception:
            identified = None

        if not identified:
            user.password_hash = hash_password(password)
            db.commit()
            db.refresh(user)

        return user, None

def register_user(full_name: str, username: str, phone: str, email: str, password: str) -> tuple[bool, str]:
    if not full_name.strip() or not username.strip():
        return False, "Họ tên và username là bắt buộc"
    if not validate_phone(phone):
        return False, "Số điện thoại không hợp lệ"
    if not validate_email(email):
        return False, "Email không hợp lệ"
    ok, msg = validate_password_strength(password)
    if not ok:
        return False, msg

    try:
        with get_db() as db:
            user = User(
                full_name=full_name.strip(),
                username=username.strip(),
                phone=phone.strip() or None,
                email=email.strip() or None,
                password_hash=hash_password(password),
                role="user",
                status="active",
            )
            db.add(user)
        return True, "Đăng ký thành công"
    except IntegrityError:
        return False, "Username, email hoặc số điện thoại đã tồn tại"


def write_audit_log(
    db: Session,
    actor_user_id: int | None,
    entity_name: str,
    entity_id: str,
    action: str,
    old_data: dict[str, Any] | None = None,
    new_data: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            entity_name=entity_name,
            entity_id=entity_id,
            action=action,
            old_data=old_data,
            new_data=new_data,
        )
    )


def calculate_price_for_room(room: Room) -> tuple[Decimal, dict[str, Any]]:
    benchmark = REGION_BENCHMARKS.get(room.khu_vuc, REGION_BENCHMARKS["ngoại thành"])
    market_avg = Decimal(str(benchmark["market_avg"]))
    market_min = Decimal(str(benchmark["market_min"]))
    market_max = Decimal(str(benchmark["market_max"]))

    adjustment = Decimal("0")
    area_adjustment = (to_decimal(room.area_m2) - Decimal("20")) * Decimal("70000")
    adjustment += area_adjustment

    amenity_points: list[tuple[str, Decimal]] = []
    if room.has_aircon:
        amenity_points.append((AMENITY_LABELS["has_aircon"], Decimal("250000")))
    if room.has_fridge:
        amenity_points.append((AMENITY_LABELS["has_fridge"], Decimal("150000")))
    if room.has_water_heater:
        amenity_points.append((AMENITY_LABELS["has_water_heater"], Decimal("180000")))
    if room.has_balcony:
        amenity_points.append((AMENITY_LABELS["has_balcony"], Decimal("120000")))
    if room.has_elevator:
        amenity_points.append((AMENITY_LABELS["has_elevator"], Decimal("180000")))

    amenity_total = sum((value for _, value in amenity_points), start=Decimal("0"))
    adjustment += amenity_total

    floor_bonus = Decimal(max(int(room.tang) - 1, 0)) * Decimal("100000")
    adjustment += floor_bonus

    suggested_price = market_avg + adjustment
    if suggested_price < market_min:
        suggested_price = market_min
    if suggested_price > market_max:
        suggested_price = market_max

    breakdown = {
        "gia_trung_binh_khu_vuc": float(market_avg),
        "khung_gia_thi_truong": {
            "thap_nhat": float(market_min),
            "cao_nhat": float(market_max),
        },
        "dieu_chinh_dien_tich": float(area_adjustment),
        "tong_tien_tien_ich": float(amenity_total),
        "thuong_tang": float(floor_bonus),
        "tien_ich_ap_dung": [name for name, _ in amenity_points],
        "nguon_tham_chieu": benchmark["reference"],
        "cong_thuc": "gia_goi_y = gia_trung_binh_khu_vuc + dieu_chinh_dien_tich + tong_tien_tien_ich + thuong_tang",
    }
    return suggested_price, breakdown


def persist_price_suggestion(db: Session, room: Room) -> PriceSuggestion:
    suggested_price, breakdown = calculate_price_for_room(room)
    item = PriceSuggestion(
        room_id=room.room_id,
        suggested_price=suggested_price,
        based_on_count=3,
        algo_version="market_reference_v2",
        score_breakdown=breakdown,
    )
    db.add(item)
    db.flush()
    return item


def create_or_update_payment(
    db: Session,
    contract_id: int,
    period: str,
    electricity_old: int,
    electricity_new: int,
    water_old: int,
    water_new: int,
    electricity_unit_price: Decimal,
    water_unit_price: Decimal,
    service_fee: Decimal,
    note: str | None,
) -> Payment:
    if electricity_new < electricity_old:
        raise ValueError("Chỉ số điện mới phải lớn hơn hoặc bằng chỉ số cũ")
    if water_new < water_old:
        raise ValueError("Chỉ số nước mới phải lớn hơn hoặc bằng chỉ số cũ")
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ValueError("Kỳ hóa đơn phải theo định dạng YYYY-MM")

    contract = db.get(Contract, contract_id)
    if not contract:
        raise ValueError("Không tìm thấy hợp đồng")

    electric_cost = Decimal(electricity_new - electricity_old) * electricity_unit_price
    water_cost = Decimal(water_new - water_old) * water_unit_price
    total = to_decimal(contract.rent_price) + electric_cost + water_cost + service_fee

    existed = db.execute(
        select(Payment).where(Payment.contract_id == contract_id, Payment.period == period)
    ).scalar_one_or_none()
    if existed:
        raise ValueError("Mỗi hợp đồng mỗi kỳ chỉ được có 1 hóa đơn")

    payment = Payment(
        contract_id=contract_id,
        period=period,
        amount=total,
        electricity_old=electricity_old,
        electricity_new=electricity_new,
        water_old=water_old,
        water_new=water_new,
        electricity_unit_price=electricity_unit_price,
        water_unit_price=water_unit_price,
        service_fee=service_fee,
        note=note,
        status="unpaid",
    )
    db.add(payment)
    db.flush()
    return payment


def mark_payment_paid(db: Session, payment_id: int, method: str) -> Payment:
    payment = db.get(Payment, payment_id)
    if not payment:
        raise ValueError("Không tìm thấy hóa đơn")
    payment.status = "paid"
    payment.method = method
    payment.paid_date = date.today()
    db.flush()
    return payment

def mark_payment_pending(db: Session, payment_id: int, method: str) -> Payment:
    payment = db.get(Payment, payment_id)
    if not payment:
        raise ValueError("Không tìm thấy hóa đơn")
    payment.status = "pending_verification"
    payment.method = method
    db.flush()
    return payment


def register_pdf_font() -> str:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("AppFont", path))
                return "AppFont"
            except Exception:
                continue
    return "Helvetica"


def build_receipt_pdf(payment: Payment) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    font_name = register_pdf_font()
    pdf.setFont(font_name, 18)
    pdf.drawString(50, 800, "PHIẾU THU TIỀN PHÒNG")
    pdf.setFont(font_name, 11)

    contract = payment.contract
    room_code = room_code_text(contract.room) if contract and contract.room else "N/A"
    tenant_name = contract.tenant.full_name if contract and contract.tenant else "N/A"

    lines = [
        f"Mã hóa đơn: P{payment.payment_id}",
        f"Phòng: {room_code}",
        f"Người thuê: {tenant_name}",
        f"Kỳ thanh toán: {payment.period}",
        f"Tổng tiền: {money(payment.amount)}",
        f"Ngày thanh toán: {payment.paid_date or ''}",
        f"Phương thức: {payment.method or ''}",
        f"Ghi chú: {payment.note or ''}",
    ]
    y = 760
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 24

    pdf.drawString(50, y - 16, "Người lập phiếu")
    pdf.drawString(360, y - 16, "Người nộp")
    pdf.save()
    buffer.seek(0)
    return buffer.read()


def build_contract_pdf(contract: Contract) -> bytes:
    buffer = io.BytesIO()
    from reportlab.lib.pagesizes import A4
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    font_name = register_pdf_font()
    
    pdf.setFont(font_name, 12)
    pdf.drawCentredString(width / 2.0, height - 50, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")
    pdf.drawCentredString(width / 2.0, height - 70, "Độc lập – Tự do – Hạnh phúc")
    
    today = contract.created_at or datetime.now()
    pdf.setFont(font_name, 11)
    pdf.drawString(width - 250, height - 100, f"......, ngày {today.day:02d} tháng {today.month:02d} năm {today.year:04d}")
    
    pdf.setFont(font_name, 14)
    pdf.drawCentredString(width / 2.0, height - 140, "PHỤ LỤC HỢP ĐỒNG THUÊ NHÀ TRỌ")
    pdf.setFont(font_name, 11)
    pdf.drawCentredString(width / 2.0, height - 160, f"Số: HD{contract.contract_id:04d}")
    
    y: float = float(height - 200)
    pdf.drawString(50, y, f"- Căn cứ hợp đồng thuê nhà trọ số: {contract.contract_id:04d}/HĐTN lập ngày {contract.start_date.day:02d} tháng {contract.start_date.month:02d} năm {contract.start_date.year:04d}")
    y -= 25.0
    pdf.drawString(50, y, "- Căn cứ vào nhu cầu thực tế và sự thỏa thuận của các bên.")
    y -= 25.0
    
    room = contract.room
    tenant = contract.tenant
    owner = room.owner if room else None
    
    addr = f"Phòng {room.room_code}" if room else ""
    if room and room.address:
        addr += f", {room.address}"
        
    pdf.drawString(50, y, f"Tại địa chỉ số: {addr}, chúng tôi gồm có:")
    y -= 30
    
    # BÊN A
    pdf.drawString(50, y, "1. Đại diện bên cho thuê phòng trọ (Bên A):")
    y -= 25
    owner_name = owner.full_name if owner else "..........................................."
    owner_phone = owner.phone if owner and owner.phone else "..........................................."
    pdf.drawString(50, y, f"Ông/bà: {owner_name}")
    pdf.drawString(300, y, "Sinh ngày: ..............................")
    y -= 25
    pdf.drawString(50, y, "Nơi đăng ký HK: ..................................................................................")
    y -= 25
    pdf.drawString(50, y, "CMND số: ..................................... cấp ngày .../.../......... tại: .......................")
    y -= 25
    pdf.drawString(50, y, f"Số điện thoại: {owner_phone}")
    y -= 30
    
    # BÊN B
    pdf.drawString(50, y, "2. Bên thuê phòng trọ (Bên B):")
    y -= 25
    tenant_name = tenant.full_name if tenant else "..........................................."
    tenant_phone = tenant.phone if tenant and tenant.phone else "..........................................."
    tenant_id = tenant.id_number if tenant and tenant.id_number else "..........................................."
    
    pdf.drawString(50, y, f"Ông/bà: {tenant_name}")
    pdf.drawString(300, y, "Sinh ngày: ..............................")
    y -= 25
    pdf.drawString(50, y, "Nơi đăng ký HK thường trú: .......................................................................")
    y -= 25
    pdf.drawString(50, y, f"Số CMND: {tenant_id}")
    pdf.drawString(300, y, "cấp ngày .../.../......... tại: .......................")
    y -= 25
    pdf.drawString(50, y, f"Số điện thoại: {tenant_phone}")
    y -= 30.0
    
    pdf.drawString(50, y, "Sau khi thống nhất, chúng tôi đồng ý sửa đổi một số nội dung cụ thể như sau:")
    y -= 25.0
    
    rent_price_str = money(contract.rent_price) if contract else ""
    deposit_str = money(contract.deposit) if contract else ""
    start_date_str = contract.start_date.strftime('%d/%m/%Y')
    end_date_str = contract.end_date.strftime('%d/%m/%Y')
    
    clauses = [
        f"1. Giá thuê phòng (sửa đổi nếu có): {rent_price_str} / kỳ ({contract.payment_cycle}).",
        f"2. Tiền cọc giữ phòng: {deposit_str}.",
        f"3. Thời hạn thuê: Từ ngày {start_date_str} đến ngày {end_date_str}.",
        "4. Điều khoản báo trước: Hai bên cam kết báo trước 30 ngày nếu dọn đi / lấy lại phòng.",
        "5. " + (contract.terms.replace('\n', ' ') if contract.terms else "Các nội dung khác được giữ nguyên theo Hợp đồng gốc.")
    ]
    
    for clause in clauses:
        c_len = 95
        chunks = [clause[i:i+c_len] for i in range(0, len(clause), c_len)]
        for chunk in chunks:
            pdf.drawString(60, y, chunk)
            y = float(y) - 20.0  # type: ignore
    
    y = float(y) - 10.0  # type: ignore
    pdf.drawString(50, y, "- Phụ lục hợp đồng được lập thành 02 bản có giá trị pháp lý như nhau, mỗi bên giữ một bản.")
    y = float(y) - 30.0  # type: ignore
    
    pdf.drawString(110, y, "ĐẠI DIỆN BÊN A")
    pdf.drawString(380, y, "ĐẠI DIỆN BÊN B")
    
    pdf.save()
    buffer.seek(0)
    return buffer.read()


def init_database() -> None:
    Base.metadata.create_all(bind=engine)
    with get_db() as db:
        admin = db.execute(select(User).where(User.username == ADMIN_USERNAME)).scalar_one_or_none()
        if not admin:
            db.add(
                User(**{
                    "full_name": ADMIN_FULL_NAME,
                    "username": ADMIN_USERNAME,
                    "password_hash": hash_password(ADMIN_PASSWORD),
                    "role": "admin",
                    "status": "active",
                })
            )


def inject_global_styles() -> None:
    st.markdown("""
<style>

/* FIX HIỂN THỊ CHỮ LUÔN */
[data-testid="stSidebar"] .stButton > button * {
    color: black !important;
}

/* nền sáng hơn để thấy chữ */
[data-testid="stSidebar"] .stButton > button {
    background: #f1f5f9 !important;
}

/* hover */
[data-testid="stSidebar"] .stButton > button:hover {
    background: #e2e8f0 !important;
}

/* button đang chọn */
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: #ff4d4f !important;
    color: white !important;
}

</style>
""", unsafe_allow_html=True)


def hero(title: str) -> None:
    st.markdown(f"<div class='hero'><h1>{title}</h1></div>", unsafe_allow_html=True)


def section_header(title: str, desc: str | None = None) -> None:
    st.markdown("<div class='card-shell'>", unsafe_allow_html=True)
    st.markdown(f"<div class='section-title'>{title}</div>", unsafe_allow_html=True)
    if desc:
        st.markdown(f"<div class='section-desc'>{desc}</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def sidebar_menu(user: SessionUser) -> str:
    with st.sidebar:
        # Logo/App name area
        st.markdown("""
<style>

/* Sidebar luôn hiển thị rõ */
[data-testid="stSidebar"] {
    width: 260px !important;
}

/* Button menu */
[data-testid="stSidebar"] .stButton > button {
    width: 100%;
    text-align: left;
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.1);
    color: #e2e8f0;
    padding: 0.7rem 1rem;
    margin: 0.3rem 0;
    border-radius: 10px;
    font-weight: 500;
    transition: all 0.2s;
}

/* Hover nhẹ thôi (không ẩn nữa) */
[data-testid="stSidebar"] .stButton > button:hover {
    background: rgba(59,130,246,0.25);
    color: white;
}

/* Button đang chọn */
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
    color: white;
    font-weight: 600;
}

/* ÉP HIỆN TOÀN BỘ TEXT (fix lỗi hover ẩn chữ) */
[data-testid="stSidebar"] * {
    opacity: 1 !important;
    visibility: visible !important;
    transform: none !important;
}

</style>
""", unsafe_allow_html=True)

        # Menu items
        if user.role == "admin":
            options = [
                "Dashboard",
                "Quản lý phòng",
                "Quản lý người thuê",
                "Quản lý hợp đồng",
                "Quản lý hóa đơn",
                "Gợi ý giá thuê",
                "Audit Log",
                "Quản lý User",
                "Trợ lý AI",
            ]
        else:
            options = [
                "Danh sách phòng",
                "Gợi ý giá thuê",
                "Hợp đồng của tôi",
                "Hóa đơn của tôi",
                "Trợ lý AI",
            ]

        # Use session state for menu selection
        if "selected_menu" not in st.session_state:
            st.session_state.selected_menu = options[0]

        # Custom CSS for button-style menu
        st.markdown("""
        <style>
        [data-testid="stSidebar"] .stButton > button {
            width: 100%;
            text-align: left;
            background: transparent;
            border: none;
            color: #e2e8f0;
            padding: 0.6rem 1rem;
            margin: 0.2rem 0;
            border-radius: 8px;
            font-weight: 500;
            transition: all 0.2s;
        }
        [data-testid="stSidebar"] .stButton > button:hover {
            background: rgba(255,255,255,0.1);
            color: white;
        }
        [data-testid="stSidebar"] .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
            color: white;
        }
        </style>
        """, unsafe_allow_html=True)

        # Navigation buttons
        st.markdown("<div style='margin-bottom: 0.5rem; color: #94a3b8; font-size: 0.75rem; font-weight: 600;'>MENU</div>", unsafe_allow_html=True)
        for option in options:
            btn_type = "primary" if st.session_state.selected_menu == option else "secondary"
            if st.button(option, key=f"nav_{option}", use_container_width=True, type=btn_type):
                st.session_state.selected_menu = option
                st.rerun()

        # User profile card at bottom
        st.markdown("---")
        st.markdown(
            f"""
            <div style="background: rgba(255,255,255,0.05); border-radius: 12px; padding: 1rem; border: 1px solid rgba(255,255,255,0.1); margin-bottom: 1rem;">
                <div style="display: flex; align-items: center; gap: 0.75rem;">
                    <div style="width: 40px; height: 40px; background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
                        border-radius: 50%; display: flex; align-items: center; justify-content: center;
                        font-size: 1.2rem; color: white; font-weight: 600;">
                        {user.full_name[0].upper() if user.full_name else '?'}
                    </div>
                    <div>
                        <div style="color: white; font-weight: 600; font-size: 0.95rem;">{user.full_name}</div>
                        <div style="color: #94a3b8; font-size: 0.8rem;">{user.username} • {user.role}</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Logout button
        if st.button("Đăng xuất", use_container_width=True):
            logout()
            st.rerun()
    return st.session_state.selected_menu


def render_auth_screen() -> None:
    # Center the login card
    left, mid, right = st.columns([1, 2, 1])
    
    with mid:
        # App branding
        st.markdown(
            """
            <div style="text-align: center; margin-bottom: 2rem;">
                <div style="font-size: 4rem; margin-bottom: 0.5rem;">🏠</div>
                <h1 style="color: #0f172a; margin: 0; font-size: 1.75rem; font-weight: 700;">Tro Gia</h1>
                <p style="color: #64748b; margin: 0.5rem 0 0;">Hệ thống quản lý phòng trọ thông minh</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        
        # Login card
        with st.container():
            st.markdown("<div class='card-shell' style='padding: 2rem;'>", unsafe_allow_html=True)
            
            tab_login, tab_register = st.tabs(["🔐 Đăng nhập", "📝 Đăng ký"])

            with tab_login:
                st.markdown("<br>", unsafe_allow_html=True)
                with st.form("login_form", clear_on_submit=False):
                    st.markdown("**Thông tin đăng nhập**")
                    login_value = st.text_input(
                        "Tên đăng nhập / Email / SĐT",
                        placeholder="Nhập tên đăng nhập...",
                    )
                    password = st.text_input(
                        "Mật khẩu",
                        type="password",
                        placeholder="Nhập mật khẩu...",
                    )
                    
                    col1, col2, col3 = st.columns([1, 2, 1])
                    with col2:
                        submitted = st.form_submit_button(
                            "Đăng nhập →",
                            type="primary",
                            use_container_width=True,
                        )
                    
                    if submitted:
                        user, err_msg = authenticate(login_value.strip(), password)
                        if err_msg:
                            st.error(f"❌ {err_msg}")
                        elif not user:
                            st.error("❌ Tên đăng nhập hoặc mật khẩu không chính xác")
                        else:
                            st.success("✅ Đăng nhập thành công!")
                            set_current_user(user)
                            time.sleep(0.5)
                            st.rerun()

            with tab_register:
                st.markdown("<br>", unsafe_allow_html=True)
                with st.form("register_form", clear_on_submit=False):
                    st.markdown("**Thông tin cá nhân**")
                    col1, col2 = st.columns(2)
                    with col1:
                        full_name = st.text_input("Họ và tên", placeholder="Nguyễn Văn A")
                        username = st.text_input("Username", placeholder="nguyenvana")
                    with col2:
                        phone = st.text_input("Số điện thoại", placeholder="0909000000")
                        email = st.text_input("Email", placeholder="email@example.com")
                    
                    st.markdown("**Bảo mật**")
                    col3, col4 = st.columns(2)
                    with col3:
                        password = st.text_input("Mật khẩu", type="password", placeholder="••••••••")
                    with col4:
                        confirm_password = st.text_input("Xác nhận mật khẩu", type="password", placeholder="••••••••")
                    
                    submitted = st.form_submit_button(
                        "Tạo tài khoản →",
                        type="primary",
                        use_container_width=True,
                    )
                    
                    if submitted:
                        if password != confirm_password:
                            st.error("❌ Mật khẩu xác nhận không khớp")
                        elif not full_name or not username or not password:
                            st.error("❌ Vui lòng điền đầy đủ thông tin bắt buộc")
                        else:
                            ok, msg = register_user(full_name, username, phone, email, password)
                            if ok:
                                st.success(f"✅ {msg}")
                            else:
                                st.error(f"❌ {msg}")
            
            st.markdown("</div>", unsafe_allow_html=True)
            
            # Footer
            st.markdown(
                """
                <div style="text-align: center; margin-top: 2rem; color: #94a3b8; font-size: 0.875rem;">
                    © 2025 Tro Gia. All rights reserved.
                </div>
                """,
                unsafe_allow_html=True,
            )


def build_dashboard_pdf(metrics: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    font_name = register_pdf_font()
    pdf.setFont(font_name, 18)
    pdf.drawString(50, 800, "BÁO CÁO TỔNG QUAN HỆ THỐNG")
    pdf.setFont(font_name, 11)
    
    lines = [
        f"Ngày xuất: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        f"Tổng số phòng: {metrics['total_rooms']}",
        f"Phòng đang thuê (Occupied): {metrics['occupied_rooms']}",
        f"Tổng số người thuê: {metrics['total_tenants']}",
        f"Công nợ chưa thu: {money(metrics['unpaid_total'])}",
        f"Doanh thu đã thu: {money(metrics['paid_total'])}",
    ]
    
    y = 760
    for line in lines:
        pdf.drawString(50, y, line)
        y -= 24
        
    pdf.save()
    buffer.seek(0)
    return buffer.read()


def render_dashboard() -> None:
    hero(APP_NAME)
    section_header("Dashboard", "Tổng quan phòng, người thuê và công nợ hiện tại.")
    with get_db() as db:
        total_rooms = db.scalar(select(func.count(Room.room_id))) or 0
        occupied_rooms = db.scalar(select(func.count(Room.room_id)).where(Room.status == "occupied")) or 0
        total_tenants = db.scalar(select(func.count(Tenant.tenant_id))) or 0
        unpaid_total = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status != "paid")) or 0
        paid_total = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "paid")) or 0
        rooms = db.execute(select(Room).order_by(Room.room_id.desc())).scalars().all()
        payments = db.execute(select(Payment).where(Payment.status == "paid")).scalars().all()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng số phòng", total_rooms)
    c2.metric("Phòng đang thuê", occupied_rooms)
    c3.metric("Tổng số người thuê", total_tenants)
    c4.metric("Công nợ chưa thu", money(unpaid_total))

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Thống kê Doanh thu theo kỳ")
        if payments:
            df_pay = pd.DataFrame([{"Kỳ": p.period, "Doanh Thu": float(p.amount)} for p in payments])
            rev_by_period = df_pay.groupby("Kỳ")["Doanh Thu"].sum().reset_index()
            st.bar_chart(rev_by_period.set_index("Kỳ"))
        else:
            st.info("Chưa có doanh thu.")
            
    with col2:
        st.markdown("#### Thống kê Tình trạng phòng")
        st.bar_chart(pd.DataFrame([{"Trạng thái": "Trống", "Số lượng": total_rooms - occupied_rooms}, {"Trạng thái": "Đang thuê", "Số lượng": occupied_rooms}]).set_index("Trạng thái"))

    st.markdown("---")
    metrics = {
        "total_rooms": total_rooms,
        "occupied_rooms": occupied_rooms,
        "total_tenants": total_tenants,
        "unpaid_total": unpaid_total,
        "paid_total": paid_total
    }
    st.download_button("Xuất Báo Cáo PDF", build_dashboard_pdf(metrics), file_name=f"bao_cao_dashboard_{date.today()}.pdf")
    st.markdown("---")

    st.markdown("#### Danh sách phòng")
    if rooms:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Mã phòng": r.room_code,
                        "Khu vực": r.khu_vuc,
                        "Diện tích": float(r.area_m2),
                        "Giá hiện tại": float(r.current_rent),
                        "Trạng thái": display_status(r.status),
                    }
                    for r in rooms
                ]
            ),
            use_container_width=True,
        )
    else:
        st.info("Chưa có dữ liệu phòng")



def render_rooms(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Quản lý phòng", "Thêm, cập nhật và tra cứu phòng trọ theo từng khu vực.")
    tabs = st.tabs(["Thêm", "Sửa", "Xóa", "Danh sách"])

    with get_db() as db:
        rooms = db.execute(
            select(Room).options(joinedload(Room.images)).order_by(Room.room_id.desc())
        ).unique().scalars().all()

    with tabs[0]:
        images_add = st.file_uploader("Hình ảnh phòng", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'], key="add_room_images")
        with st.form("add_room_form"):
            c1, c2 = st.columns(2)
            with c1:
                room_code = st.text_input("Mã phòng")
                area_m2 = st.number_input("Diện tích (m²)", min_value=1.0, value=20.0, step=1.0)
                khu_vuc = st.selectbox("Khu vực", list(REGION_BENCHMARKS.keys()))
                tang = st.number_input("Tầng", min_value=1, value=1, step=1)
            with c2:
                address = st.text_input("Địa chỉ")
                current_rent = st.number_input("Giá hiện tại", min_value=0.0, value=2500000.0, step=100000.0)
                st.write("")
                a1, a2, a3 = st.columns(3)
                has_aircon = a1.checkbox("Máy lạnh")
                has_fridge = a2.checkbox("Tủ lạnh")
                has_water_heater = a3.checkbox("Bình nóng lạnh")
                a4, a5 = st.columns(2)
                has_balcony = a4.checkbox("Ban công")
                has_elevator = a5.checkbox("Thang máy")
            submitted = st.form_submit_button("Thêm phòng", type="primary")
        if submitted:
            room_code_clean = clean_text(room_code)
            if not room_code_clean:
                st.error("Mã phòng không được để trống")
            else:
                try:
                    with get_db() as db:
                        room = Room(
                            owner_id=user.user_id,
                            room_code=room_code_clean,
                            area_m2=to_decimal(area_m2),
                            khu_vuc=khu_vuc,
                            tang=int(tang),
                            address=clean_text(address) or None,
                            current_rent=to_decimal(current_rent),
                            status="available",
                            has_aircon=has_aircon,
                            has_fridge=has_fridge,
                            has_water_heater=has_water_heater,
                            has_balcony=has_balcony,
                            has_elevator=has_elevator,
                        )
                        db.add(room)
                        db.flush()
                        
                        if images_add:
                            img_dir = os.path.join(os.getcwd(), "static", "images")
                            os.makedirs(img_dir, exist_ok=True)
                            for img in images_add:
                                ext = img.name.split(".")[-1]
                                filename = f"{uuid.uuid4().hex}.{ext}"
                                filepath = os.path.join(img_dir, filename)
                                with open(filepath, "wb") as f:
                                    f.write(img.getbuffer())
                                room_image = RoomImage(room_id=room.room_id, image_url=filepath)
                                db.add(room_image)
                                
                        write_audit_log(db, user.user_id, "rooms", str(room.room_id), "insert", new_data=serialize_model(room))
                    st.success("Thêm phòng thành công")
                    st.rerun()
                except IntegrityError:
                    st.error("Mã phòng đã tồn tại")

    with tabs[1]:
        if not rooms:
            st.info("Chưa có phòng để sửa")
        else:
            room_map = {room_label(r): r.room_id for r in rooms}
            selected = st.selectbox("Chọn phòng", list(room_map.keys()))
            with get_db() as db:
                room = db.get(Room, room_map[selected], options=[joinedload(Room.images)])
                if room is None:
                    st.error("Không tìm thấy phòng")
                else:
                    # Show current images with delete buttons
                    if room.images:
                        st.markdown("#### 📸 Ảnh hiện tại của phòng")
                        img_cols = st.columns(min(len(room.images), 4))
                        for j, img_obj in enumerate(room.images):
                            with img_cols[j % 4]:
                                if os.path.exists(img_obj.image_url):
                                    st.image(img_obj.image_url, use_container_width=True)
                                else:
                                    st.caption("(file bị mất)")
                                if st.button("🗑️ Xóa", key=f"del_img_{img_obj.image_id}"):
                                    with get_db() as del_db:
                                        img_to_del = del_db.get(RoomImage, img_obj.image_id)
                                        if img_to_del:
                                            try:
                                                if os.path.exists(img_to_del.image_url):
                                                    os.remove(img_to_del.image_url)
                                            except Exception:
                                                pass
                                            del_db.delete(img_to_del)
                                    st.success("Đã xóa ảnh")
                                    st.rerun()
                    else:
                        st.info("Phòng này chưa có ảnh nào.")

                    # Edit form
                    with st.form("edit_room_form"):
                        c1, c2 = st.columns(2)
                        with c1:
                            room_code = st.text_input("Mã phòng", value=room.room_code or "")
                            area_m2 = st.number_input("Diện tích (m²)", min_value=1.0, value=float(room.area_m2), step=1.0)
                            khu_vuc = st.selectbox(
                                "Khu vực", list(REGION_BENCHMARKS.keys()), index=list(REGION_BENCHMARKS.keys()).index(room.khu_vuc)
                            )
                            tang = st.number_input("Tầng", min_value=1, value=int(room.tang), step=1)
                        with c2:
                            address = st.text_input("Địa chỉ", value=room.address or "")
                            current_rent = st.number_input("Giá hiện tại", min_value=0.0, value=float(room.current_rent), step=100000.0)
                            status = st.selectbox("Trạng thái", ["Còn trống", "Đã thuê"], index=0 if room.status == "available" else 1)
                        a1, a2, a3, a4, a5 = st.columns(5)
                        has_aircon = a1.checkbox("Máy lạnh", value=room.has_aircon)
                        has_fridge = a2.checkbox("Tủ lạnh", value=room.has_fridge)
                        has_water_heater = a3.checkbox("Bình nóng lạnh", value=room.has_water_heater)
                        has_balcony = a4.checkbox("Ban công", value=room.has_balcony)
                        has_elevator = a5.checkbox("Thang máy", value=room.has_elevator)
                        submitted = st.form_submit_button("Cập nhật phòng", type="primary")

                    images_edit = st.file_uploader("📤 Thêm ảnh mới cho phòng", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'], key=f"edit_room_images_{room.room_id}")

                    if submitted:
                        room_code_clean = clean_text(room_code)
                        if not room_code_clean:
                            st.error("Mã phòng không được để trống")
                        else:
                            old_data = serialize_model(room)
                            room.room_code = room_code_clean
                            room.area_m2 = to_decimal(area_m2)
                            room.khu_vuc = khu_vuc
                            room.tang = int(tang)
                            room.address = clean_text(address) or None
                            room.current_rent = to_decimal(current_rent)
                            room.status = db_status(status)
                            room.has_aircon = has_aircon
                            room.has_fridge = has_fridge
                            room.has_water_heater = has_water_heater
                            room.has_balcony = has_balcony
                            room.has_elevator = has_elevator
                            try:
                                db.flush()
                                
                                if images_edit:
                                    img_dir = os.path.join(os.getcwd(), "static", "images")
                                    os.makedirs(img_dir, exist_ok=True)
                                    for img in images_edit:
                                        ext = img.name.split(".")[-1]
                                        filename = f"{uuid.uuid4().hex}.{ext}"
                                        filepath = os.path.join(img_dir, filename)
                                        with open(filepath, "wb") as f:
                                            f.write(img.getbuffer())
                                        room_image = RoomImage(room_id=room.room_id, image_url=filepath)
                                        db.add(room_image)
                                        
                                write_audit_log(db, user.user_id, "rooms", str(room.room_id), "update", old_data, serialize_model(room))
                                st.success("Cập nhật phòng thành công")
                                st.rerun()
                            except IntegrityError:
                                st.error("Mã phòng đã tồn tại")

    with tabs[2]:
        if not rooms:
            st.info("Chưa có phòng để xóa")
        else:
            room_map = {room_label(r): r.room_id for r in rooms}
            selected = st.selectbox("Chọn phòng cần xóa", list(room_map.keys()), key="delete_room")
            if st.button("Xóa phòng"):
                with get_db() as db:
                    room = db.get(Room, room_map[selected])
                    linked_contract = db.execute(
                        select(Contract).where(Contract.room_id == room.room_id).limit(1)
                    ).scalar_one_or_none()
                    if linked_contract:
                        st.error("Không thể xóa phòng đang có hợp đồng active")
                    else:
                        old = serialize_model(room)
                        room_id = room.room_id
                        db.delete(room)
                        write_audit_log(db, user.user_id, "rooms", str(room_id), "delete", old_data=old)
                        st.success("Đã xóa phòng")
                        st.rerun()

    with tabs[3]:
        if rooms:
            c1, c2, c3 = st.columns([2, 1, 1])
            keyword = c1.text_input("Tìm kiếm phòng", placeholder="Nhập mã phòng, khu vực, địa chỉ...")
            status_filter = c2.selectbox("Lọc trạng thái", ["Tất cả"] + sorted({display_status(r.status) for r in rooms}))
            region_filter = c3.selectbox("Lọc khu vực", ["Tất cả"] + sorted({r.khu_vuc for r in rooms}))

            filtered_rooms = []
            for r in rooms:
                if keyword and keyword.lower() not in normalize_room_search(r):
                    continue
                if status_filter != "Tất cả" and r.status != db_status(status_filter):
                    continue
                if region_filter != "Tất cả" and r.khu_vuc != region_filter:
                    continue
                filtered_rooms.append(r)

            if not filtered_rooms:
                st.info("Không có phòng phù hợp với bộ lọc")
            else:
                cols = st.columns(3)
                for i, r in enumerate(filtered_rooms):
                    suggested_price, _ = calculate_price_for_room(r)
                    with cols[i % 3]:
                        with st.container(border=True):
                            if r.images:
                                img_path = r.images[0].image_url
                                if os.path.exists(img_path):
                                    st.image(img_path, width=400)
                                else:
                                    st.image("https://placehold.co/400x250/e2e8f0/94a3b8?text=Phòng+Trọ", width=400)
                            else:
                                st.image("https://placehold.co/400x250/e2e8f0/94a3b8?text=Phòng+Trọ", width=400)
                            st.markdown(f"### {room_code_text(r)}")
                            st.markdown(f"📍 **Khu vực:** {r.khu_vuc}")
                            st.markdown(f"📏 **Diện tích:** {float(r.area_m2)} m²  |  🏢 **Tầng:** {r.tang}")
                            st.markdown(f"💰 **Giá:** {money(r.current_rent)}  →  🤖 AI: {money(suggested_price)}")
                            amenities = []
                            if r.has_aircon: amenities.append("Máy lạnh")
                            if r.has_fridge: amenities.append("Tủ lạnh")
                            if r.has_water_heater: amenities.append("Nóng lạnh")
                            if r.has_balcony: amenities.append("Ban công")
                            if r.has_elevator: amenities.append("Thang máy")
                            if amenities:
                                st.markdown(f"✨ {', '.join(amenities)}")
                            status_color = "green" if r.status == "available" else "red"
                            status_text = "Còn trống" if r.status == "available" else "Đang thuê"
                            st.markdown(f"**Trạng thái:** :{status_color}[{status_text}]")
                            if r.images:
                                st.caption(f"📸 {len(r.images)} ảnh")
        else:
            st.info("Chưa có dữ liệu phòng")



def render_tenants(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Quản lý người thuê", "Lưu hồ sơ người thuê và liên kết với tài khoản người dùng khi cần.")
    tabs = st.tabs(["Thêm", "Sửa", "Xóa", "Danh sách"])
    with get_db() as db:
        tenants = (
            db.execute(
                select(Tenant)
                .options(joinedload(Tenant.user))
                .order_by(Tenant.tenant_id.desc())
            )
            .scalars()
            .all()
        )
        users = db.execute(select(User).where(User.role == "user").order_by(User.username)).scalars().all()

    user_options = {"Không liên kết": None}
    for u in users:
        user_options[f"{u.username} • {u.full_name}"] = u.user_id

    with tabs[0]:
        with st.form("add_tenant_form"):
            c1, c2 = st.columns(2)
            with c1:
                full_name = st.text_input("Họ tên người thuê")
                phone = st.text_input("Số điện thoại")
                email = st.text_input("Email")
            with c2:
                id_number = st.text_input("CCCD/CMND")
                address = st.text_input("Địa chỉ")
                selected_user = st.selectbox("Liên kết tài khoản user", list(user_options.keys()))
            submitted = st.form_submit_button("Thêm người thuê", type="primary")
        if submitted:
            if not clean_text(full_name):
                st.error("Họ tên người thuê là bắt buộc")
            elif not validate_phone(phone):
                st.error("Số điện thoại không hợp lệ")
            elif not validate_email(email):
                st.error("Email không hợp lệ")
            else:
                linked_user_id = user_options[selected_user]
                try:
                    with get_db() as db:
                        tenant = Tenant(
                            user_id=linked_user_id,
                            full_name=clean_text(full_name),
                            phone=clean_text(phone) or None,
                            email=clean_text(email) or None,
                            id_number=clean_text(id_number) or None,
                            address=clean_text(address) or None,
                        )
                        db.add(tenant)
                        db.flush()
                        write_audit_log(db, user.user_id, "tenants", str(tenant.tenant_id), "insert", new_data=serialize_model(tenant))
                    st.success("Thêm người thuê thành công")
                    st.rerun()
                except IntegrityError:
                    st.error("Tài khoản user này đã được liên kết hoặc dữ liệu trùng")

    with tabs[1]:
        if not tenants:
            st.info("Chưa có người thuê để sửa")
        else:
            tenant_map = {tenant_label(t): t.tenant_id for t in tenants}
            selected = st.selectbox("Chọn người thuê", list(tenant_map.keys()))
            with get_db() as db:
                tenant = db.execute(
                    select(Tenant)
                    .options(joinedload(Tenant.user))
                    .where(Tenant.tenant_id == tenant_map[selected])
                ).scalar_one()
                current_user_label = "Không liên kết"
                for label, value in user_options.items():
                    if value == tenant.user_id:
                        current_user_label = label
                        break
                with st.form("edit_tenant_form"):
                    c1, c2 = st.columns(2)
                    with c1:
                        full_name = st.text_input("Họ tên", value=tenant.full_name)
                        phone = st.text_input("Số điện thoại", value=tenant.phone or "")
                        email = st.text_input("Email", value=tenant.email or "")
                    with c2:
                        id_number = st.text_input("CCCD/CMND", value=tenant.id_number or "")
                        address = st.text_input("Địa chỉ", value=tenant.address or "")
                        selected_user = st.selectbox(
                            "Liên kết tài khoản user",
                            list(user_options.keys()),
                            index=list(user_options.keys()).index(current_user_label),
                        )
                    submitted = st.form_submit_button("Cập nhật người thuê", type="primary")
                if submitted:
                    if not clean_text(full_name):
                        st.error("Họ tên người thuê là bắt buộc")
                    elif not validate_phone(phone):
                        st.error("Số điện thoại không hợp lệ")
                    elif not validate_email(email):
                        st.error("Email không hợp lệ")
                    else:
                        old = serialize_model(tenant)
                        tenant.full_name = clean_text(full_name)
                        tenant.phone = clean_text(phone) or None
                        tenant.email = clean_text(email) or None
                        tenant.id_number = clean_text(id_number) or None
                        tenant.address = clean_text(address) or None
                        tenant.user_id = user_options[selected_user]
                        try:
                            db.flush()
                            write_audit_log(db, user.user_id, "tenants", str(tenant.tenant_id), "update", old, serialize_model(tenant))
                            st.success("Cập nhật người thuê thành công")
                            st.rerun()
                        except IntegrityError:
                            st.error("Tài khoản user này đã được liên kết hoặc dữ liệu trùng")

    with tabs[2]:
        if not tenants:
            st.info("Chưa có người thuê để xóa")
        else:
            tenant_map = {tenant_label(t): t.tenant_id for t in tenants}
            selected = st.selectbox("Chọn người thuê cần xóa", list(tenant_map.keys()), key="delete_tenant")
            if st.button("Xóa người thuê"):
                with get_db() as db:
                    tenant = db.get(Tenant, tenant_map[selected])
                    active_contract = db.execute(
                        select(Contract).where(Contract.tenant_id == tenant.tenant_id)
                    ).scalar_one_or_none()
                    if active_contract:
                        st.error("Không thể xóa người thuê đã có hợp đồng")
                    else:
                        old = serialize_model(tenant)
                        tenant_id = tenant.tenant_id
                        db.delete(tenant)
                        write_audit_log(db, user.user_id, "tenants", str(tenant_id), "delete", old_data=old)
                        st.success("Đã xóa người thuê")
                        st.rerun()

    with tabs[3]:
        if tenants:
            c1, c2 = st.columns([2, 1])
            keyword = c1.text_input("Tìm kiếm người thuê", placeholder="Họ tên, SĐT, Email, CCCD...")
            linked_filter = c2.selectbox("Lọc liên kết tài khoản", ["Tất cả", "Đã liên kết", "Chưa liên kết"])
            
            filtered_tenants = []
            for t in tenants:
                if keyword:
                    kw = keyword.lower()
                    if kw not in (t.full_name or "").lower() and kw not in (t.phone or "").lower() and kw not in (t.email or "").lower() and kw not in (t.id_number or "").lower():
                        continue
                if linked_filter == "Đã liên kết" and not t.user:
                    continue
                if linked_filter == "Chưa liên kết" and t.user:
                    continue
                filtered_tenants.append(t)

            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "ID": t.tenant_id,
                            "Họ tên": t.full_name,
                            "SĐT": t.phone,
                            "Email": t.email,
                            "CCCD": t.id_number,
                            "Tài khoản liên kết": t.user.username if t.user else "Chưa liên kết",
                        }
                        for t in filtered_tenants
                    ]
                ),
                use_container_width=True,
            )
        else:
            st.info("Chưa có dữ liệu người thuê")



def render_contracts(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Quản lý hợp đồng", "Theo dõi thông tin thuê phòng, thời hạn và trạng thái hợp đồng.")
    tabs = st.tabs(["Tạo hợp đồng", "Kết thúc hợp đồng", "Danh sách"])
    with get_db() as db:
        rooms = db.execute(select(Room).order_by(Room.room_id.desc())).scalars().all()
        tenants = db.execute(
            select(Tenant)
            .options(joinedload(Tenant.user))
            .order_by(Tenant.full_name)
        ).scalars().all()
        contracts = db.execute(
            select(Contract)
            .options(joinedload(Contract.room), joinedload(Contract.tenant).joinedload(Tenant.user))
            .order_by(Contract.contract_id.desc())
        ).scalars().all()

    active_room_ids = {c.room_id for c in contracts if c.status == "active"}
    room_options_map = {
        f"{room_code_text(r)} • {r.khu_vuc} • {money(r.current_rent)}": r.room_id
        for r in rooms
        if r.room_id not in active_room_ids
    }
    tenant_options_map = {
        tenant_label(t): t.tenant_id for t in tenants
    }

    with tabs[0]:
        if not room_options_map or not tenant_options_map:
            st.info("Cần có phòng trống và dữ liệu người thuê trước khi tạo hợp đồng")
        else:
            with st.form("add_contract_form"):
                c1, c2 = st.columns(2)
                with c1:
                    room_label_selected = st.selectbox("Chọn phòng trống", list(room_options_map.keys()))
                    tenant_label_selected = st.selectbox("Chọn người thuê", list(tenant_options_map.keys()))
                    start_date = st.date_input("Ngày bắt đầu", value=date.today())
                with c2:
                    default_end = date(date.today().year + 1, date.today().month, min(date.today().day, 28))
                    end_date = st.date_input("Ngày kết thúc", value=default_end)
                    room_selected_id = room_options_map[room_label_selected]
                    selected_room = next((r for r in rooms if r.room_id == room_selected_id), None)
                    default_rent = float(selected_room.current_rent) if selected_room else 2500000.0
                    rent_price = st.number_input("Giá thuê", min_value=0.0, value=default_rent, step=100000.0)
                    deposit = st.number_input("Tiền cọc", min_value=0.0, value=1000000.0, step=100000.0)
                payment_cycle = st.selectbox("Chu kỳ thanh toán", ["monthly"])
                terms = st.text_area("Điều khoản")
                digital_signature = st.text_input("Chữ ký số / Mã tham chiếu")
                submitted = st.form_submit_button("Tạo hợp đồng", type="primary")

            selected_tenant = next((t for t in tenants if t.tenant_id == tenant_options_map[tenant_label_selected]), None)
            if selected_tenant and not selected_tenant.user_id:
                st.warning("Người thuê này chưa liên kết tài khoản user. Hợp đồng sẽ chưa hiện ở màn hình người dùng cho đến khi được liên kết.")

            if submitted:
                ok, msg = validate_contract_dates(start_date, end_date)
                if not ok:
                    st.error(msg)
                else:
                    with get_db() as db:
                        room = db.get(Room, room_options_map[room_label_selected])
                        tenant = db.execute(
                            select(Tenant)
                            .options(joinedload(Tenant.user))
                            .where(Tenant.tenant_id == tenant_options_map[tenant_label_selected])
                        ).scalar_one()
                        if not tenant.user_id:
                            candidate = None
                            if tenant.email:
                                candidate = db.execute(select(User).where(User.email == tenant.email)).scalar_one_or_none()
                            if not candidate and tenant.phone:
                                candidate = db.execute(select(User).where(User.phone == tenant.phone)).scalar_one_or_none()
                            if candidate:
                                tenant.user_id = candidate.user_id
                                db.flush()
                        active = db.execute(
                            select(Contract).where(Contract.room_id == room.room_id, Contract.status == "active")
                        ).scalar_one_or_none()
                        if active:
                            st.error("Phòng này đang có hợp đồng active")
                        else:
                            contract = Contract(
                                room_id=room.room_id,
                                tenant_id=tenant.tenant_id,
                                start_date=start_date,
                                end_date=end_date,
                                rent_price=to_decimal(rent_price),
                                deposit=to_decimal(deposit),
                                payment_cycle=payment_cycle,
                                status="active",
                                terms=clean_text(terms) or None,
                                digital_signature=clean_text(digital_signature) or None,
                            )
                            db.add(contract)
                            db.flush()
                            old_room = serialize_model(room)
                            room.status = "occupied"
                            room.current_rent = to_decimal(rent_price)
                            db.flush()
                            write_audit_log(
                                db,
                                user.user_id,
                                "contracts",
                                str(contract.contract_id),
                                "insert",
                                new_data=serialize_model(contract),
                            )
                            write_audit_log(
                                db,
                                user.user_id,
                                "rooms",
                                str(room.room_id),
                                "update",
                                old_data=old_room,
                                new_data=serialize_model(room),
                            )
                            st.success("Tạo hợp đồng thành công")
                            if tenant.user_id:
                                st.info(f"Hợp đồng đã gắn với tài khoản user: {tenant.user.username}")
                            else:
                                st.info("Hợp đồng đã tạo. Hãy liên kết người thuê với tài khoản user để bên user nhìn thấy hợp đồng.")
                            
                            st.session_state["recent_contract_id"] = contract.contract_id
                            st.rerun()

            if st.session_state.get("recent_contract_id"):
                cid = st.session_state["recent_contract_id"]
                with get_db() as db:
                    recent_c = db.execute(
                        select(Contract)
                        .where(Contract.contract_id == cid)
                        .options(joinedload(Contract.room).joinedload(Room.owner), joinedload(Contract.tenant))
                    ).scalar_one_or_none()
                    
                    if recent_c:
                        st.info("Hợp đồng vừa được tạo thành công! Vui lòng tải file PDF bên dưới:")
                        st.download_button(
                            "Tải file hợp đồng PDF", 
                            build_contract_pdf(recent_c), 
                            file_name=f"hop_dong_HD{recent_c.contract_id}.pdf", 
                            mime="application/pdf",
                            type="primary",
                        )
                        if st.button("Tạo hợp đồng khác"):
                            del st.session_state["recent_contract_id"]
                            st.rerun()

    with tabs[1]:
        active_contracts = [c for c in contracts if c.status == "active"]
        if not active_contracts:
            st.info("Không có hợp đồng active")
        else:
            options = {f"HD{c.contract_id} • {room_code_text(c.room)} • {c.tenant.full_name}": c.contract_id for c in active_contracts}
            selected = st.selectbox("Chọn hợp đồng cần kết thúc", list(options.keys()))
            if st.button("Kết thúc hợp đồng"):
                with get_db() as db:
                    contract = db.execute(
                        select(Contract)
                        .options(joinedload(Contract.room).joinedload(Room.owner), joinedload(Contract.tenant))
                        .where(Contract.contract_id == options[selected])
                    ).scalar_one()
                    old = serialize_model(contract)
                    old_room = serialize_model(contract.room)
                    contract.status = "ended"
                    contract.room.status = "available"
                    db.flush()
                    write_audit_log(db, user.user_id, "contracts", str(contract.contract_id), "update", old, serialize_model(contract))
                    write_audit_log(db, user.user_id, "rooms", str(contract.room.room_id), "update", old_room, serialize_model(contract.room))
                    st.success("Đã kết thúc hợp đồng")
                    st.rerun()

    with tabs[2]:
        if contracts:
            c1, c2 = st.columns([2, 1])
            keyword = c1.text_input("Tìm kiếm hợp đồng", placeholder="Tên người thuê, mã HĐ, mã phòng...")
            status_filter = c2.selectbox("Lọc trạng thái", ["Tất cả", "active", "ended"])
            
            filtered_contracts = []
            for c in contracts:
                if status_filter != "Tất cả" and c.status != status_filter:
                    continue
                if keyword:
                    kw = keyword.lower()
                    if kw not in f"hd{c.contract_id}".lower() and kw not in room_code_text(c.room).lower() and kw not in c.tenant.full_name.lower():
                        continue
                filtered_contracts.append(c)

            contract_rows = [
                {
                    "Mã HĐ": c.contract_id,
                    "Phòng": room_code_text(c.room),
                    "Người thuê": c.tenant.full_name,
                    "Tài khoản user": c.tenant.user.username if c.tenant and c.tenant.user else "Chưa liên kết",
                    "Bắt đầu": c.start_date,
                    "Kết thúc": c.end_date,
                    "Giá thuê": float(c.rent_price),
                    "Tiền cọc": float(c.deposit),
                    "Trạng thái": c.status,
                }
                for c in filtered_contracts
            ]
            st.dataframe(pd.DataFrame(contract_rows), use_container_width=True, hide_index=True)

            export_map = {f"HD{c.contract_id} • {room_code_text(c.room)} • {c.tenant.full_name}": c.contract_id for c in contracts}
            selected_export = st.selectbox("Xuất hợp đồng PDF", list(export_map.keys()))
            if st.button("Tạo file hợp đồng PDF"):
                with get_db() as db:
                    contract = db.execute(
                        select(Contract)
                        .options(joinedload(Contract.room).joinedload(Room.owner), joinedload(Contract.tenant))
                        .where(Contract.contract_id == export_map[selected_export])
                    ).scalar_one()
                    pdf_bytes = build_contract_pdf(contract)
                st.download_button(
                    "Tải hợp đồng PDF",
                    pdf_bytes,
                    file_name=f"hop_dong_{export_map[selected_export]}.pdf",
                    mime="application/pdf",
                    key=f"download_contract_admin_{export_map[selected_export]}",
                )
        else:
            st.info("Chưa có hợp đồng")


def render_payments(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Quản lý hóa đơn", "Lập hóa đơn định kỳ, ghi nhận thanh toán và xuất phiếu thu.")
    tabs = st.tabs(["Lập hóa đơn", "Ghi nhận thanh toán", "Xuất phiếu thu PDF", "Danh sách"])
    with get_db() as db:
        contracts = db.execute(
            select(Contract)
            .where(Contract.status == "active")
            .options(joinedload(Contract.room).joinedload(Room.owner), joinedload(Contract.tenant))
            .order_by(Contract.contract_id.desc())
        ).scalars().all()
        payments = db.execute(
            select(Payment)
            .options(joinedload(Payment.contract).joinedload(Contract.room), joinedload(Payment.contract).joinedload(Contract.tenant))
            .order_by(Payment.payment_id.desc())
        ).scalars().all()

    with tabs[0]:
        if not contracts:
            st.info("Chưa có hợp đồng active để lập hóa đơn")
        else:
            options = {f"HD{c.contract_id} - {room_code_text(c.room)} - {c.tenant.full_name}": c.contract_id for c in contracts}
            with st.form("payment_form"):
                contract_label = st.selectbox("Chọn hợp đồng", list(options.keys()))
                c1, c2, c3 = st.columns(3)
                period = c1.text_input("Kỳ hóa đơn (YYYY-MM)", value=date.today().strftime("%Y-%m"))
                electricity_old = c2.number_input("Điện cũ", min_value=0, value=0, step=1)
                electricity_new = c3.number_input("Điện mới", min_value=0, value=0, step=1)
                c4, c5, c6 = st.columns(3)
                water_old = c4.number_input("Nước cũ", min_value=0, value=0, step=1)
                water_new = c5.number_input("Nước mới", min_value=0, value=0, step=1)
                service_fee = c6.number_input("Phí dịch vụ", min_value=0.0, value=0.0, step=50000.0)
                c7, c8 = st.columns(2)
                electricity_unit_price = c7.number_input("Đơn giá điện", min_value=0.0, value=3500.0, step=500.0)
                water_unit_price = c8.number_input("Đơn giá nước", min_value=0.0, value=15000.0, step=1000.0)
                note = st.text_area("Ghi chú")
                submitted = st.form_submit_button("Lập hóa đơn", type="primary")
            if submitted:
                try:
                    with get_db() as db:
                        payment = create_or_update_payment(
                            db,
                            options[contract_label],
                            period.strip(),
                            int(electricity_old),
                            int(electricity_new),
                            int(water_old),
                            int(water_new),
                            to_decimal(electricity_unit_price),
                            to_decimal(water_unit_price),
                            to_decimal(service_fee),
                            note.strip() or None,
                        )
                        write_audit_log(db, user.user_id, "payments", str(payment.payment_id), "insert", new_data=serialize_model(payment))

                        # Auto notify user about new payment
                        try:
                            notify_payment_created(db, payment)
                        except Exception:
                            pass  # Notification failure shouldn't block payment creation

                    st.success(f"Đã lập hóa đơn. Tổng tiền: {money(payment.amount)}")
                    st.rerun()
                except ValueError as ex:
                    st.error(str(ex))

    with tabs[1]:
        pending = [p for p in payments if p.status == "pending_verification"]
        unpaid = [p for p in payments if p.status == "unpaid"]
        
        st.markdown("### ⏳ Hóa đơn chờ xác nhận (MoMo/CK)")
        if pending:
            for p in pending:
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 2, 1])
                    c1.write(f"**HD#{p.payment_id}** - Phòng {room_code_text(p.contract.room)} - Kỳ {p.period}")
                    c2.markdown(f"**{money(p.amount)}** :orange[({p.method})]")
                    if c3.button("✅ Xác nhận", key=f"confirm_pending_{p.payment_id}", use_container_width=True):
                        with get_db() as db:
                            payment = mark_payment_paid(db, p.payment_id, p.method)
                            write_audit_log(db, user.user_id, "payments", str(payment.payment_id), "payment", new_data=serialize_model(payment))
                            # Notify user about payment confirmation
                            try:
                                notify_payment_paid(db, payment, confirmed_by_admin=True)
                            except Exception:
                                pass  # Notification failure shouldn't block
                        st.success("Đã xác nhận thanh toán và thông báo cho người thuê")
                        st.rerun()
        else:
            st.info("Không có hóa đơn nào đang chờ xác nhận")
            
        st.markdown("---")
        st.markdown("### 📝 Ghi nhận thanh toán thủ công")
        if not unpaid:
            st.info("Không có hóa đơn chưa thanh toán")
        else:
            options = {f"P{p.payment_id} - {room_code_text(p.contract.room)} - {p.period} - {money(p.amount)}": p.payment_id for p in unpaid}
            selected = st.selectbox("Chọn hóa đơn", list(options.keys()))
            method = st.selectbox("Phương thức", ["cash", "bank", "qr"])
            if st.button("Ghi nhận đã thanh toán"):
                with get_db() as db:
                    payment = mark_payment_paid(db, options[selected], method)
                    write_audit_log(db, user.user_id, "payments", str(payment.payment_id), "payment", new_data=serialize_model(payment))
                    st.success("Đã ghi nhận thanh toán")
                    st.rerun()

    with tabs[2]:
        paid = [p for p in payments if p.status == "paid"]
        if not paid:
            st.info("Chưa có hóa đơn đã thanh toán để xuất phiếu thu")
        else:
            options = {f"P{p.payment_id} - {room_code_text(p.contract.room)} - {p.period}": p.payment_id for p in paid}
            selected = st.selectbox("Chọn hóa đơn đã thanh toán", list(options.keys()))
            if st.button("Tạo phiếu thu PDF"):
                with get_db() as db:
                    payment = db.execute(
                        select(Payment)
                        .where(Payment.payment_id == options[selected])
                        .options(joinedload(Payment.contract).joinedload(Contract.room), joinedload(Payment.contract).joinedload(Contract.tenant))
                    ).scalar_one()
                    pdf_bytes = build_receipt_pdf(payment)
                st.download_button("Tải phiếu thu PDF", pdf_bytes, file_name=f"phieu_thu_{options[selected]}.pdf", mime="application/pdf")

    with tabs[3]:
        if payments:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Mã hóa đơn": p.payment_id,
                            "Phòng": room_code_text(p.contract.room),
                            "Người thuê": p.contract.tenant.full_name,
                            "Kỳ": p.period,
                            "Số tiền": float(p.amount),
                            "Trạng thái": p.status,
                            "Ngày thanh toán": p.paid_date,
                            "Phương thức": p.method,
                        }
                        for p in payments
                    ]
                ),
                use_container_width=True,
            )
        else:
            st.info("Chưa có hóa đơn")


def render_price_suggestion(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Gợi ý giá thuê", "Đề xuất giá theo khu vực thị trường và đặc điểm của từng phòng.")
    with get_db() as db:
        rooms = db.execute(select(Room).order_by(Room.room_code)).scalars().all()
        suggestions = db.execute(
            select(PriceSuggestion).options(joinedload(PriceSuggestion.room)).order_by(PriceSuggestion.suggestion_id.desc())
        ).scalars().all()

    if not rooms:
        st.info("Chưa có phòng để tính giá")
        return

    options = {f"{room_code_text(r)} - {r.khu_vuc}": r.room_id for r in rooms}
    selected = st.selectbox("Chọn phòng", list(options.keys()))

    with get_db() as db:
        room = db.get(Room, options[selected])
        suggested_price, breakdown = calculate_price_for_room(room)
        benchmark = breakdown["khung_gia_thi_truong"]

        c1, c2, c3 = st.columns(3)
        c1.metric("Giá hiện tại", money(room.current_rent))
        c2.metric("Trung bình khu vực", money(breakdown["gia_trung_binh_khu_vuc"]))
        c3.metric("Giá gợi ý", money(suggested_price), delta=money(suggested_price - room.current_rent))

        st.markdown("---")
        st.markdown("**Chi tiết tham chiếu và cách tính**")
        d1, d2, d3 = st.columns(3)
        d1.metric("Giá thấp nhất khu vực", money(benchmark["thap_nhat"]))
        d2.metric("Giá cao nhất khu vực", money(benchmark["cao_nhat"]))
        d3.metric("Điều chỉnh diện tích", money(breakdown["dieu_chinh_dien_tich"]))
        e1, e2 = st.columns(2)
        e1.metric("Tổng tiện ích", money(breakdown["tong_tien_tien_ich"]))
        e2.metric("Thưởng tầng", money(breakdown["thuong_tang"]))
        st.write("**Tiện ích áp dụng:**", ", ".join(breakdown["tien_ich_ap_dung"]) or "Không có")
        st.caption(f"Nguồn tham chiếu: {breakdown['nguon_tham_chieu']}")

        if st.button("Lưu lịch sử gợi ý giá", type="primary"):
            item = persist_price_suggestion(db, room)
            write_audit_log(db, user.user_id, "price_suggestions", str(item.suggestion_id), "insert", new_data=serialize_model(item))
            st.success("Đã lưu lịch sử gợi ý giá")
            st.rerun()

    st.markdown("#### Lịch sử gợi ý giá")
    if suggestions:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "ID": s.suggestion_id,
                        "Phòng": room_code_text(s.room),
                        "Giá gợi ý": float(s.suggested_price),
                        "Số mẫu tham chiếu": s.based_on_count,
                        "Phiên bản thuật toán": s.algo_version,
                        "Thời gian": s.created_at,
                    }
                    for s in suggestions
                ]
            ),
            use_container_width=True,
        )
    else:
        st.info("Chưa có lịch sử gợi ý giá")



def render_audit_logs() -> None:
    hero(APP_NAME)
    section_header("Audit Log", "Theo dõi lịch sử thêm, sửa, xóa và thanh toán trên hệ thống.")
    with get_db() as db:
        logs = (
            db.execute(
                select(AuditLog)
                .options(joinedload(AuditLog.actor))
                .order_by(AuditLog.changed_at.desc(), AuditLog.audit_id.desc())
            )
            .scalars()
            .all()
        )

    if not logs:
        st.info("Chưa có audit log")
        return

    entity_options = ["Tất cả"] + sorted({ENTITY_LABELS.get(log.entity_name, log.entity_name) for log in logs})
    action_options = ["Tất cả"] + sorted({ACTION_LABELS.get(log.action, log.action) for log in logs})

    c1, c2, c3 = st.columns([1, 1, 1.5])
    selected_entity_label = c1.selectbox("Lọc theo bảng", entity_options)
    selected_action_label = c2.selectbox("Lọc theo hành động", action_options)
    keyword = c3.text_input("Tìm kiếm log", placeholder="Tên bảng, người thao tác, ID, nội dung...")

    filtered_logs = logs
    if selected_entity_label != "Tất cả":
        reverse_entity = {v: k for k, v in ENTITY_LABELS.items()}
        selected_entity = reverse_entity.get(selected_entity_label, selected_entity_label)
        filtered_logs = [log for log in filtered_logs if log.entity_name == selected_entity]
    if selected_action_label != "Tất cả":
        reverse_action = {v: k for k, v in ACTION_LABELS.items()}
        selected_action = reverse_action.get(selected_action_label, selected_action_label)
        filtered_logs = [log for log in filtered_logs if log.action == selected_action]
    if keyword:
        kw = keyword.lower()
        filtered_logs = [
            log for log in filtered_logs
            if kw in str(log.audit_id).lower()
            or kw in (log.entity_name or "").lower()
            or kw in (log.entity_id or "").lower()
            or kw in (log.actor.username.lower() if log.actor else "hệ thống")
            or kw in summarize_audit_data(log.entity_name, log.old_data).lower()
            or kw in summarize_audit_data(log.entity_name, log.new_data).lower()
        ]

    m1, m2, m3 = st.columns(3)
    m1.metric("Tổng log", len(filtered_logs))
    m2.metric("Thêm mới", sum(1 for log in filtered_logs if log.action == "insert"))
    m3.metric("Cập nhật/Xóa/Thanh toán", sum(1 for log in filtered_logs if log.action in {"update", "delete", "payment"}))

    table_rows = []
    for log in filtered_logs:
        table_rows.append(
            {
                "ID": log.audit_id,
                "Thời gian": log.changed_at.strftime("%d/%m/%Y %H:%M:%S") if log.changed_at else "",
                "Người thao tác": log.actor.username if log.actor else "Hệ thống",
                "Bảng": ENTITY_LABELS.get(log.entity_name, log.entity_name),
                "ID đối tượng": log.entity_id,
                "Hành động": ACTION_LABELS.get(log.action, log.action),
                "Dữ liệu cũ": summarize_audit_data(log.entity_name, log.old_data),
                "Dữ liệu mới": summarize_audit_data(log.entity_name, log.new_data),
            }
        )
    audit_df = pd.DataFrame(table_rows)
    if not audit_df.empty:
        for col in ["Dữ liệu cũ", "Dữ liệu mới"]:
            audit_df[col] = audit_df[col].apply(lambda v: (v[:120] + "...") if isinstance(v, str) and len(v) > 120 else v)
    st.dataframe(audit_df, use_container_width=True, hide_index=True)

    st.markdown("### Xem chi tiết log")
    choice_map = {
        f"Log #{log.audit_id} • {ENTITY_LABELS.get(log.entity_name, log.entity_name)} • {ACTION_LABELS.get(log.action, log.action)}": log
        for log in filtered_logs
    }
    selected_label = st.selectbox("Chọn log", list(choice_map.keys()))
    selected_log = choice_map[selected_label]

    i1, i2, i3, i4 = st.columns(4)
    i1.write(f"**Thời gian:** {selected_log.changed_at.strftime('%d/%m/%Y %H:%M:%S') if selected_log.changed_at else ''}")
    i2.write(f"**Người thao tác:** {selected_log.actor.username if selected_log.actor else 'Hệ thống'}")
    i3.write(f"**Bảng:** {ENTITY_LABELS.get(selected_log.entity_name, selected_log.entity_name)}")
    i4.write(f"**Hành động:** {ACTION_LABELS.get(selected_log.action, selected_log.action)}")

    d1, d2 = st.columns(2)
    with d1:
        st.markdown("#### Dữ liệu cũ")
        if selected_log.old_data:
            st.dataframe(audit_kv_frame(selected_log.old_data), use_container_width=True, hide_index=True)
        else:
            st.info("Không có dữ liệu cũ")
    with d2:
        st.markdown("#### Dữ liệu mới")
        if selected_log.new_data:
            st.dataframe(audit_kv_frame(selected_log.new_data), use_container_width=True, hide_index=True)
        else:
            st.info("Không có dữ liệu mới")

    csv_bytes = pd.DataFrame(table_rows).to_csv(index=False).encode("utf-8-sig")
    st.download_button("Tải CSV Audit Log", csv_bytes, file_name="audit_log.csv", mime="text/csv")


def render_user_room_catalog(user: SessionUser) -> None:
    hero(APP_NAME)

    with get_db() as db:
        rooms = db.execute(
            select(Room).options(joinedload(Room.images)).order_by(Room.room_id.desc())
        ).unique().scalars().all()

    if not rooms:
        st.info("Chưa có dữ liệu phòng")
        return

    # 1. Detail View Mode
    detail_room_id = st.session_state.get("detail_room_id")
    if detail_room_id:
        detail_room = next((r for r in rooms if r.room_id == detail_room_id), None)
        if detail_room:
            if st.button("⬅️ Quay lại danh sách phòng", key="close_detail_top"):
                del st.session_state["detail_room_id"]
                if f"room_chat_{detail_room_id}" in st.session_state:
                    del st.session_state[f"room_chat_{detail_room_id}"]
                st.rerun()

            # Load owner (admin) info for contact
            with get_db() as db:
                owner = db.get(User, detail_room.owner_id)

            st.markdown(f"## 🏠 {room_code_text(detail_room)}")
            status_badge = '<span style="background:#22c55e;color:white;padding:2px 10px;border-radius:12px;font-size:0.85rem;">Còn trống</span>' if detail_room.status == "available" else '<span style="background:#ef4444;color:white;padding:2px 10px;border-radius:12px;font-size:0.85rem;">Đang thuê</span>'
            st.markdown(status_badge, unsafe_allow_html=True)
            st.markdown("")

            col_img, col_info = st.columns([1.3, 1])
            with col_img:
                if detail_room.images:
                    valid_imgs = [img for img in detail_room.images if os.path.exists(img.image_url)]
                    if valid_imgs:
                        for img in valid_imgs[:3]:
                            st.image(img.image_url, use_container_width=True)
                    else:
                        st.image("https://placehold.co/600x400/e2e8f0/94a3b8?text=Phòng+Trọ", use_container_width=True)
                else:
                    st.image("https://placehold.co/600x400/e2e8f0/94a3b8?text=Phòng+Trọ", use_container_width=True)

            with col_info:
                st.markdown("### 📋 Thông tin chi tiết")
                st.markdown(f"- **Mã phòng:** {detail_room.room_code}")
                st.markdown(f"- **Khu vực:** {detail_room.khu_vuc}")
                st.markdown(f"- **Địa chỉ:** {detail_room.address or 'Liên hệ admin'}")
                st.markdown(f"- **Diện tích:** {float(detail_room.area_m2)} m²")
                st.markdown(f"- **Tầng:** {detail_room.tang}")

                st.markdown("---")
                st.markdown("### 💰 Giá thuê")
                suggested, _ = calculate_price_for_room(detail_room)
                st.markdown(f"<div style='font-size:1.4rem;font-weight:700;color:#3b82f6;'>{money(detail_room.current_rent)}<span style='font-size:0.9rem;color:#94a3b8;'>/tháng</span></div>", unsafe_allow_html=True)
                st.caption(f"AI gợi ý: {money(suggested)}/tháng")

                st.markdown("---")
                st.markdown("### ✨ Tiện ích")
                amenity_list = []
                if detail_room.has_aircon: amenity_list.append("❄️ Máy lạnh")
                if detail_room.has_fridge: amenity_list.append("🧊 Tủ lạnh")
                if detail_room.has_water_heater: amenity_list.append("🚿 Bình nóng lạnh")
                if detail_room.has_balcony: amenity_list.append("🌿 Ban công")
                if detail_room.has_elevator: amenity_list.append("🛗 Thang máy")
                if amenity_list:
                    cols_am = st.columns(2)
                    for idx, a in enumerate(amenity_list):
                        cols_am[idx % 2].markdown(f"  {a}")
                else:
                    st.markdown("  Cơ bản")

            # --- Contact Panel ---
            st.markdown("---")
            st.markdown("### 📞 Thông tin liên hệ")
            contact_cols = st.columns([1.5, 1, 1])
            owner_name = owner.full_name if owner else "Chủ trọ"
            owner_phone = owner.phone if owner else MOMO_PHONE
            with contact_cols[0]:
                st.markdown(
                    f"""<div style="border:1px solid #334155;border-radius:12px;padding:1rem;">
                    <div style="font-weight:600;font-size:1rem;">👤 {owner_name}</div>
                    <div style="color:#94a3b8;font-size:0.85rem;">Chủ nhà / Quản lý</div>
                    <div style="margin-top:0.4rem;font-size:0.95rem;">📱 <b>{owner_phone or 'Liên hệ qua hệ thống'}</b></div>
                    </div>""",
                    unsafe_allow_html=True,
                )
            with contact_cols[1]:
                if owner_phone:
                    st.link_button(f"📞 Gọi điện", f"tel:{owner_phone}", use_container_width=True, type="primary")
                else:
                    st.button("📞 Gọi điện", disabled=True, use_container_width=True)
            with contact_cols[2]:
                chat_key = f"show_chat_{detail_room_id}"
                if st.button("💬 Nhắn tin với Admin", key=f"open_chat_{detail_room_id}", use_container_width=True):
                    st.session_state[chat_key] = not st.session_state.get(chat_key, False)
                    st.rerun()

            # --- Inline Chat with Auto-Reply ---
            chat_session_key = f"room_chat_{detail_room_id}"
            if st.session_state.get(chat_key, False):
                st.markdown("#### 💬 Nhắn tin về phòng này")
                st.caption("Trả lời tự động các câu hỏi cơ bản. Câu hỏi về hợp đồng/thanh toán sẽ chuyển đến admin.")

                if chat_session_key not in st.session_state:
                    st.session_state[chat_session_key] = [
                        {"role": "bot", "text": (
                            f"Xin chào! Tôi là trợ lý tự động của phòng **{detail_room.room_code}**.\n\n"
                            f"Bạn có thể hỏi tôi về:\n"
                            f"- Giá thuê, diện tích, tầng\n"
                            f"- Tiện ích (máy lạnh, wifi...)\n"
                            f"- Tình trạng còn trống\n"
                            f"- Địa chỉ / khu vực\n\n"
                            f"Câu hỏi về hợp đồng hoặc thanh toán sẽ được chuyển đến admin."
                        )}
                    ]

                chat_msgs = st.session_state[chat_session_key]
                with st.container():
                    for msg in chat_msgs:
                        if msg["role"] == "user":
                            with st.chat_message("user"):
                                st.markdown(msg["text"])
                        else:
                            with st.chat_message("assistant"):
                                st.markdown(msg["text"])

                if user_msg := st.chat_input(f"Hỏi về phòng {detail_room.room_code}...", key=f"chat_input_{detail_room_id}"):
                    chat_msgs.append({"role": "user", "text": user_msg})

                    reply, forward_to_admin = room_contact_auto_reply(user_msg, detail_room)
                    chat_msgs.append({"role": "bot", "text": reply})

                    # Forward to admin if needed
                    if forward_to_admin:
                        with get_db() as db:
                            admin = db.execute(select(User).where(User.role == "admin").limit(1)).scalar_one_or_none()
                            if admin:
                                from services.notification_service import send_user_to_admin_message
                                send_user_to_admin_message(
                                    db,
                                    user_id=user.user_id,
                                    admin_user_id=admin.user_id,
                                    title=f"Khách hỏi về phòng {detail_room.room_code}",
                                    message=f"{user.full_name}: {user_msg}",
                                    notification_type="general",
                                    related_entity_type="room",
                                    related_entity_id=detail_room.room_id,
                                )

                    st.session_state[chat_session_key] = chat_msgs
                    st.rerun()

            return # Early return so we don't render the grid

    # 2. List View Mode
    section_header("Danh sách phòng", "Tra cứu nhanh phòng và mức giá gợi ý để so sánh trước khi thuê.")
    
    c1, c2, c3 = st.columns([2, 1, 1])
    keyword = c1.text_input("Tìm kiếm danh sách phòng", placeholder="Mã phòng, khu vực, địa chỉ...")
    status_filter = c2.selectbox("Trạng thái phòng", ["Tất cả"] + sorted({display_status(r.status) for r in rooms}))
    region_filter = c3.selectbox("Khu vực", ["Tất cả"] + sorted({r.khu_vuc for r in rooms}))

    filtered_rooms = []
    for room in rooms:
        if keyword and keyword.lower() not in normalize_room_search(room):
            continue
        if status_filter != "Tất cả" and room.status != db_status(status_filter):
            continue
        if region_filter != "Tất cả" and room.khu_vuc != region_filter:
            continue
        filtered_rooms.append(room)

    st.markdown("---")
    if not filtered_rooms:
        st.info("Không có phòng phù hợp với bộ lọc")
        return

    cols = st.columns(3)
    for i, room in enumerate(filtered_rooms):
        suggested_price, _ = calculate_price_for_room(room)
        with cols[i % 3]:
            with st.container(border=True):
                if room.images:
                    img_path = room.images[0].image_url
                    if os.path.exists(img_path):
                        st.image(img_path, width=400)
                    else:
                        st.image("https://placehold.co/400x250/e2e8f0/94a3b8?text=Phòng+Trọ", width=400)
                else:
                    st.image("https://placehold.co/400x250/e2e8f0/94a3b8?text=Phòng+Trọ", width=400)
                st.markdown(f"### {room_code_text(room)}")
                st.markdown(f"📍 **Khu vực:** {room.khu_vuc}")
                st.markdown(f"📏 **Diện tích:** {float(room.area_m2)} m²")
                st.markdown(f"🏢 **Tầng:** {room.tang}")
                st.markdown(f"💰 **Giá thuê:** {money(room.current_rent)}")
                
                amenities = []
                if room.has_aircon: amenities.append("Máy lạnh")
                if room.has_fridge: amenities.append("Tủ lạnh")
                if room.has_water_heater: amenities.append("Nóng lạnh")
                if amenities:
                    st.markdown(f"✨ **Tiện ích:** {', '.join(amenities)}")
                
                status_color = "green" if room.status == "available" else "red"
                status_text = "Còn trống" if room.status == "available" else "Đang thuê"
                st.markdown(f"**Trạng thái:** :{status_color}[{status_text}]")

                if st.button(f"Xem chi tiết #{room.room_id}", key=f"view_room_{room.room_id}", use_container_width=True):
                    st.session_state["detail_room_id"] = room.room_id
                    st.rerun()


def render_user_price_suggestion(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Gợi ý giá thuê", "Xem mức giá khuyến nghị và phân tích AI nội bộ cho phòng bạn quan tâm hoặc đang thuê.")
    with get_db() as db:
        tenant = resolve_tenant_for_user(db, user)
        contracts = []
        if tenant:
            contracts = db.execute(
                select(Contract)
                .where(Contract.tenant_id == tenant.tenant_id, Contract.status == "active")
                .options(joinedload(Contract.room))
                .order_by(Contract.contract_id.desc())
            ).scalars().all()
        rooms = db.execute(select(Room).order_by(Room.room_id.desc())).scalars().all()

    if not rooms:
        st.info("Chưa có dữ liệu phòng")
        return

    preferred_room_id = contracts[0].room_id if contracts else None
    options = {f"{room_code_text(r)} • {r.khu_vuc} • {r.status}": r.room_id for r in rooms}
    labels = list(options.keys())
    default_index = 0
    if preferred_room_id:
        for idx, label in enumerate(labels):
            if options[label] == preferred_room_id:
                default_index = idx
                break
    selected = st.selectbox("Chọn phòng để xem gợi ý", labels, index=default_index)

    with get_db() as db:
        room = db.get(Room, options[selected])
        suggested_price, breakdown = calculate_price_for_room(room)

    c1, c2, c3 = st.columns(3)
    c1.metric("Giá hiện tại", money(room.current_rent))
    c2.metric("Giá gợi ý", money(suggested_price))
    c3.metric("Khung thị trường", f"{money(breakdown['khung_gia_thi_truong']['thap_nhat'])} - {money(breakdown['khung_gia_thi_truong']['cao_nhat'])}")

    st.info(generate_ai_price_advice(room, suggested_price, breakdown))

    e1, e2 = st.columns(2)
    with e1:
        st.markdown("#### Thành phần tính giá")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Yếu tố": "Giá trung bình khu vực", "Giá trị": money(breakdown.get('gia_trung_binh_khu_vuc', 0))},
                    {"Yếu tố": "Điều chỉnh diện tích", "Giá trị": money(breakdown.get('dieu_chinh_dien_tich', 0))},
                    {"Yếu tố": "Tổng tiện ích", "Giá trị": money(breakdown.get('tong_tien_tien_ich', 0))},
                    {"Yếu tố": "Thưởng tầng", "Giá trị": money(breakdown.get('thuong_tang', 0))},
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    with e2:
        st.markdown("#### Tiện ích được tính")
        st.write(", ".join(breakdown.get("tien_ich_ap_dung") or ["Không có"]))
        st.caption(f"Nguồn tham chiếu: {breakdown.get('nguon_tham_chieu', 'Nội bộ')}")


def render_user_contracts(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Hợp đồng của tôi", "Xem thông tin hợp đồng thuê và điều khoản liên quan.")
    with get_db() as db:
        tenant = resolve_tenant_for_user(db, user)
        if not tenant:
            st.info("Tài khoản này chưa liên kết hồ sơ người thuê")
            return
        contracts = db.execute(
            select(Contract)
            .where(Contract.tenant_id == tenant.tenant_id)
            .options(joinedload(Contract.room).joinedload(Room.owner), joinedload(Contract.tenant))
            .order_by(Contract.contract_id.desc())
        ).scalars().all()
    if not contracts:
        st.info("Bạn chưa có hợp đồng thuê nào")
        return
    for c in contracts:
        with st.container(border=True):
            c1, c2 = st.columns(2)
            c1.write(f"**Mã hợp đồng:** HD{c.contract_id}")
            c1.write(f"**Phòng:** {room_code_text(c.room)}")
            c1.write(f"**Thời hạn:** {c.start_date} → {c.end_date}")
            c2.write(f"**Giá thuê:** {money(c.rent_price)}")
            c2.write(f"**Trạng thái:** {c.status}")
            c2.write(f"**Chữ ký số:** {c.digital_signature or '---'}")
            st.write(f"**Điều khoản:** {c.terms or '---'}")
            st.download_button(
                f"Tải hợp đồng PDF HD{c.contract_id}",
                build_contract_pdf(c),
                file_name=f"hop_dong_{c.contract_id}.pdf",
                mime="application/pdf",
                key=f"contract_pdf_user_{c.contract_id}",
            )


def generate_momo_qr(amount: int, note: str) -> io.BytesIO | None:
    """Generate MoMo QR code using VietQR/MoMo deeplink format."""
    encoded_note = urllib.parse.quote(note)
    # MoMo deeplink format for scanning
    momo_url = (
        f"2|99|{MOMO_PHONE}|{MOMO_NAME}||0|0|{amount}|{encoded_note}"
    )
    if not QRCODE_AVAILABLE:
        return None
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
        qr.add_data(momo_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#ae2070", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception:
        return None


def generate_vietqr_url(amount: int, note: str) -> str:
    """Generate VietQR URL for bank transfer (uses napas247 format)."""
    if not BANK_ACCOUNT_NO or not BANK_NAME:
        return ""
    encoded_note = urllib.parse.quote(note)
    return (
        f"https://img.vietqr.io/image/{BANK_NAME}-{BANK_ACCOUNT_NO}-compact2.png"
        f"?amount={amount}&addInfo={encoded_note}&accountName={urllib.parse.quote(BANK_ACCOUNT_NAME)}"
    )


def render_payment_notification_banner(user: SessionUser) -> None:
    """Hiển thị banner thông báo thanh toán ở đầu trang khi user có hóa đơn chưa trả."""
    with get_db() as db:
        tenant = resolve_tenant_for_user(db, user)
        if not tenant:
            return
        unpaid_payments = db.execute(
            select(Payment)
            .join(Payment.contract)
            .where(Contract.tenant_id == tenant.tenant_id, Payment.status == "unpaid")
            .options(joinedload(Payment.contract).joinedload(Contract.room))
            .order_by(Payment.period.asc())
        ).scalars().all()
        overdue_count = db.scalar(
            select(func.count(Payment.payment_id))
            .join(Payment.contract)
            .where(Contract.tenant_id == tenant.tenant_id, Payment.status == "overdue")
        ) or 0

    if not unpaid_payments and overdue_count == 0:
        return

    total_unpaid = sum(p.amount for p in unpaid_payments)
    bills_count = len(unpaid_payments) + overdue_count

    # Lấy hóa đơn cũ nhất để nhanh chóng thanh toán
    oldest_unpaid = unpaid_payments[0] if unpaid_payments else None

    if overdue_count > 0:
        banner_color = "#fef2f2"
        border_color = "#fca5a5"
        icon = "🚨"
        status_text = f"QUÁ HẠN / chưa thanh toán"
    else:
        banner_color = "#fffbeb"
        border_color = "#fcd34d"
        icon = "⚠️"
        status_text = "chưa thanh toán"

    st.markdown(
        f"""
        <div style="
            background: {banner_color};
            border: 2px solid {border_color};
            border-radius: 16px;
            padding: 1rem 1.4rem;
            margin-bottom: 1rem;
            display: flex;
            align-items: center;
            gap: 1rem;
        ">
            <div style="font-size:2rem">{icon}</div>
            <div style="flex:1">
                <div style="font-weight:800;font-size:1.05rem;color:#92400e">
                    Bạn có <b>{bills_count} hóa đơn</b> {status_text}!
                </div>
                <div style="color:#78350f;margin-top:.2rem;font-size:.93rem">
                    Tổng cần thanh toán: <b>{money(total_unpaid)}</b>  —  Vui lòng thanh toán sớm để tránh phát sinh phí.
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if oldest_unpaid:
        if st.button(
            f"💳 Thanh toán nhanh hóa đơn #{oldest_unpaid.payment_id} — Kỳ {oldest_unpaid.period} ({money(oldest_unpaid.amount)})",
            key="quick_pay_banner",
            type="primary",
        ):
            st.session_state["payment_to_pay"] = oldest_unpaid.payment_id
            st.rerun()


@st.dialog("Thanh toán hóa đơn", width="large")
def render_payment_dialog(payment, user):
    """Modal dialog for payment with MoMo and Cash options"""
    st.write(f"**Hóa đơn #{payment.payment_id} — Kỳ {payment.period}**")
    st.write(f"🏠 Phòng: {room_code_text(payment.contract.room)}")
    st.write(f"💰 Số tiền: **{money(payment.amount)}**")
    st.divider()
    
    # Payment method selection
    st.write("**Chọn phương thức thanh toán:**")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📱 Thanh toán MoMo", key=f"momo_{payment.payment_id}", use_container_width=True, type="primary"):
            st.session_state[f"show_momo_{payment.payment_id}"] = True
            st.session_state[f"show_cash_{payment.payment_id}"] = False
            st.rerun()
    
    with col2:
        if st.button("💵 Thanh toán tiền mặt", key=f"cash_btn_{payment.payment_id}", use_container_width=True):
            st.session_state[f"show_momo_{payment.payment_id}"] = False
            st.session_state[f"show_cash_{payment.payment_id}"] = True
            st.rerun()
    
    st.divider()
    
    # Initialize session states if not exist
    if f"show_momo_{payment.payment_id}" not in st.session_state:
        st.session_state[f"show_momo_{payment.payment_id}"] = False
    if f"show_cash_{payment.payment_id}" not in st.session_state:
        st.session_state[f"show_cash_{payment.payment_id}"] = False
    
    # Show QR for MoMo
    if st.session_state.get(f"show_momo_{payment.payment_id}", False):
        st.markdown("<center>", unsafe_allow_html=True)
        st.warning("📱 Quét mã QR để thanh toán qua MoMo")
        
        note = f"Phong {payment.contract.room.room_code} ky {payment.period} HD{payment.contract_id}"
        amount_int = int(payment.amount)
        
        # Generate QR
        try:
            import base64
            encoded_note = urllib.parse.quote(note)
            momo_url = f"2|99|{MOMO_PHONE}|{MOMO_NAME}||0|0|{amount_int}|{encoded_note}"
            
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(momo_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="#ae2070", back_color="white")
            
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            img_base64 = base64.b64encode(buf.getvalue()).decode()
            
            st.markdown(
                f"""<div style="text-align:center;background:#fff;border-radius:12px;padding:1rem; margin: 1rem 0;">
                <img src="data:image/png;base64,{img_base64}" width="220" style="border-radius:8px;" />
                <div style="color:#ae2070;font-weight:600;margin-top:10px;font-size:1.1rem;">SĐT: {MOMO_PHONE}</div>
                <div style="color:#666;font-size:0.9rem;">Chủ tài khoản: {MOMO_NAME}</div>
                <div style="color:#333;font-size:0.85rem;margin-top:8px;">Nội dung: <code>{note}</code></div>
                </div>""",
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.error(f"❌ Lỗi tạo QR: {e}")
        
        st.info("⏳ Sau khi thanh toán, vui lòng nhấn 'Xác nhận' bên dưới")
        
        if st.button("✅ Tôi đã thanh toán", key=f"confirm_momo_{payment.payment_id}", type="primary", use_container_width=True):
            with get_db() as db:
                p_updated = mark_payment_paid(db, payment.payment_id, "momo_qr")
                write_audit_log(
                    db, user.user_id, "payments", str(p_updated.payment_id),
                    "payment", new_data=serialize_model(p_updated)
                )
                admin = db.execute(select(User).where(User.role == "admin").limit(1)).scalar_one_or_none()
                if admin:
                    create_notification(
                        db,
                        sender_id=user.user_id,
                        recipient_id=admin.user_id,
                        title=f"Thanh toán MoMo - Phòng {payment.contract.room.room_code}",
                        message=f"Khách {user.full_name} đã thanh toán {money(payment.amount)} (kỳ {payment.period}) qua MoMo.",
                        notification_type="payment"
                    )
                notify_payment_paid(db, p_updated, confirmed_by_admin=False)
            st.success("🎉 Thanh toán thành công! Admin sẽ xác nhận sớm.")
            st.session_state[f"show_momo_{payment.payment_id}"] = False
            time.sleep(1)
            st.rerun()
        st.markdown("</center>", unsafe_allow_html=True)
    
    # Show info for Cash
    if st.session_state.get(f"show_cash_{payment.payment_id}", False):
        st.info("""
        💵 **Thanh toán tiền mặt**
        
        Vui lòng liên hệ chủ trọ để thanh toán trực tiếp.
        
        📞 **SĐT:** 0909 000 000  
        📍 **Địa chỉ:** 123 Đường ABC, Quận XYZ  
        ⏰ **Giờ làm việc:** 8:00 - 20:00 hàng ngày
        """)
        
        if st.button("📞 Tôi sẽ thanh toán trực tiếp", key=f"confirm_cash_{payment.payment_id}", use_container_width=True):
            st.session_state[f"show_cash_{payment.payment_id}"] = False
            st.success("✅ Đã ghi nhận! Vui lòng thanh toán với chủ trọ.")
            time.sleep(1)
            st.rerun()


def render_user_payments(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Hóa đơn của tôi", "Theo dõi các kỳ thanh toán và lịch sử hóa đơn cá nhân.")
    with get_db() as db:
        tenant = resolve_tenant_for_user(db, user)
        if not tenant:
            st.info("Tài khoản này chưa liên kết hồ sơ người thuê")
            return
        payments = db.execute(
            select(Payment)
            .join(Payment.contract)
            .where(Contract.tenant_id == tenant.tenant_id)
            .options(joinedload(Payment.contract).joinedload(Contract.room), joinedload(Payment.contract).joinedload(Contract.tenant))
            .order_by(Payment.payment_id.desc())
        ).scalars().all()
    if not payments:
        st.info("Chưa có hóa đơn nào")
        return

    unpaid = [p for p in payments if p.status == "unpaid"]
    pending = [p for p in payments if p.status == "pending_verification"]
    paid = [p for p in payments if p.status == "paid"]

    # Summary metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Tổng hóa đơn", len(payments))
    m2.metric("⚠️ Phải nợ / Chờ xác nhận", len(unpaid) + len(pending))
    m3.metric("✅ Đã thanh toán", len(paid))

    st.markdown("---")

    # Unpaid invoices first
    if unpaid:
        st.markdown("### ⚠️ Hóa đơn chưa thanh toán")
        for p in unpaid:
            with st.container(border=True):
                c1, c2 = st.columns([2, 1])
                with c1:
                    st.markdown(f"#### 🧾 Hóa đơn #{p.payment_id} — Kỳ {p.period}")
                    ic1, ic2 = st.columns(2)
                    ic1.markdown(f"🏠 **Phòng:** {room_code_text(p.contract.room)}")
                    ic1.markdown(f"⚡ **Điện:** {p.electricity_old} → {p.electricity_new} số")
                    ic2.markdown(f"💧 **Nước:** {p.water_old} → {p.water_new} m³")
                    ic2.markdown(f"💰 **Tổng tiền: {money(p.amount)}**")
                    st.markdown(f":red[**Trạng thái: Chưa thanh toán**]")

                with c2:
                    # Payment Dialog
                    if st.button("💳 Thanh toán", key=f"pay_btn_{p.payment_id}", use_container_width=True, type="primary"):
                        render_payment_dialog(p, user)

    # Pending invoices
    if pending:
        st.markdown("---")
        st.markdown("### ⏳ Hóa đơn đang chờ xác nhận")
        for p in pending:
            with st.container(border=True):
                c1, c2, c3 = st.columns(3)
                c1.markdown(f"🧾 **HD#{p.payment_id}** — Kỳ {p.period}")
                c1.markdown(f"🏠 {room_code_text(p.contract.room)}")
                c2.markdown(f"💰 **{money(p.amount)}**")
                c2.markdown(f"⚡ Điện: {p.electricity_old}→{p.electricity_new}  |  💧 Nước: {p.water_old}→{p.water_new}")
                c3.markdown(":orange[⏳ Đang chờ Admin xác nhận...]")

    # Paid invoices
    if paid:
        st.markdown("---")
        st.markdown("### ✅ Lịch sử đã thanh toán")
        for p in paid:
            with st.container(border=True):
                c1, c2, c3 = st.columns(3)
                c1.markdown(f"🧾 **HD#{p.payment_id}** — Kỳ {p.period}")
                c1.markdown(f"🏠 {room_code_text(p.contract.room)}")
                c2.markdown(f"💰 **{money(p.amount)}**")
                c2.markdown(f"⚡ Điện: {p.electricity_old}→{p.electricity_new}  |  💧 Nước: {p.water_old}→{p.water_new}")
                c3.markdown(":green[✅ Đã thanh toán]")
                if p.paid_date:
                    c3.markdown(f"📅 {p.paid_date.strftime('%d/%m/%Y') if hasattr(p.paid_date, 'strftime') else p.paid_date}")


def render_user_payment_page(user: SessionUser, payment_id: int) -> None:
    hero(APP_NAME)
    if st.button("⬅️ Quay lại danh sách hóa đơn"):
        st.session_state.pop("payment_to_pay", None)
        st.rerun()

    with get_db() as db:
        payment = db.execute(
            select(Payment)
            .options(
                joinedload(Payment.contract).joinedload(Contract.room),
                joinedload(Payment.contract).joinedload(Contract.tenant),
            )
            .where(Payment.payment_id == payment_id)
        ).scalar_one_or_none()

    if not payment:
        st.error("Không tìm thấy hóa đơn")
        return

    room_code = room_code_text(payment.contract.room)
    note = f"Phong {payment.contract.room.room_code} ky {payment.period} HD{payment.contract_id}"
    amount_int = int(payment.amount)

    section_header(
        f"Thanh toán hóa đơn #{payment.payment_id}",
        f"Kỳ {payment.period} — Phòng {room_code}",
    )

    # ── Chi tiết hóa đơn ──────────────────────────────────────────────────────
    st.markdown(
        f"""
        <div style="background:#eff6ff;border:1.5px solid #bfdbfe;border-radius:16px;padding:1rem 1.4rem;margin-bottom:1rem">
            <h4 style="margin:0 0 .6rem">🧾 Chi tiết hóa đơn</h4>
            <table style="width:100%;border-collapse:collapse;font-size:.97rem">
                <tr><td style="padding:.25rem .5rem;color:#475569">Phòng</td><td style="font-weight:700">{room_code}</td></tr>
                <tr><td style="padding:.25rem .5rem;color:#475569">Kỳ thanh toán</td><td style="font-weight:700">{payment.period}</td></tr>
                <tr><td style="padding:.25rem .5rem;color:#475569">⚡ Điện</td><td>{payment.electricity_old} → {payment.electricity_new} số ({money((payment.electricity_new - payment.electricity_old) * payment.electricity_unit_price)})</td></tr>
                <tr><td style="padding:.25rem .5rem;color:#475569">💧 Nước</td><td>{payment.water_old} → {payment.water_new} m³ ({money((payment.water_new - payment.water_old) * payment.water_unit_price)})</td></tr>
                <tr><td style="padding:.25rem .5rem;color:#475569">Tiền thuê</td><td>{money(payment.contract.rent_price)}</td></tr>
                <tr><td style="padding:.25rem .5rem;color:#475569">Phí dịch vụ</td><td>{money(payment.service_fee)}</td></tr>
                <tr style="border-top:2px solid #93c5fd"><td style="padding:.5rem .5rem;font-weight:800;color:#1e40af;font-size:1.1rem">💰 TỔNG CỘNG</td><td style="font-weight:800;color:#1e40af;font-size:1.1rem">{money(payment.amount)}</td></tr>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Phương thức thanh toán ────────────────────────────────────────────────
    st.markdown("### 💳 Chọn phương thức thanh toán")
    payment_method = st.radio(
        "Phương thức",
        ["📱 Chuyển khoản / Quét QR", "💵 Tiền mặt"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if payment_method == "📱 Chuyển khoản / Quét QR":
        # ── MoMo Static QR only ──────────────────────────────────────────────
        st.markdown("### 📷 Quét mã QR MoMo để thanh toán")
        col_qr, col_info = st.columns([1, 1.3])
        with col_qr:
            # Static QR override
            static_qr_path = os.path.join(os.getcwd(), "static", "images", "qr_thanh_toan.png")
            if os.path.exists(static_qr_path):
                st.image(static_qr_path, width=240, caption="📱 Quét mã QR để thanh toán")
            else:
                qr_buf = generate_momo_qr(amount_int, note)
                if qr_buf:
                    st.image(qr_buf, width=240, caption="📱 Quét bằng ứng dụng MoMo")
                else:
                    st.warning("⚙️ Cài đặt thư viện `qrcode` để hiển thị mã QR tự động")
                    st.caption(f"Hoặc mở app MoMo và chuyển tới SĐT: **{MOMO_PHONE}**")

        with col_info:
            st.markdown("#### 📋 Thông tin chuyển khoản MoMo")
            st.markdown(
                f"""
                | Thông tin | Chi tiết |
                |---|---|
                | 📱 Số điện thoại | `{MOMO_PHONE}` |
                | 👤 Tên | **{MOMO_NAME}** |
                | 💰 Số tiền | **{money(payment.amount)}** |
                | 📝 Nội dung CK | `{note}` |
                """
            )
            st.info("⚠️ Vui lòng nhập **đúng nội dung chuyển khoản** để Admin xác nhận nhanh.")

        st.markdown("---")
        st.info("💡 Sau khi chuyển khoản, nhấn nút bên dưới để hoàn tất thanh toán tự động.")
        if st.button(
            "✅ Tôi đã chuyển khoản - Hoàn tất ngay",
            key=f"confirm_momo_{payment.payment_id}",
            type="primary",
            use_container_width=True,
        ):
            with get_db() as db:
                # Auto-complete immediately (no admin approval needed)
                p_updated = mark_payment_paid(db, payment.payment_id, "qr_auto")
                write_audit_log(
                    db, user.user_id, "payments", str(p_updated.payment_id),
                    "payment", new_data=serialize_model(p_updated)
                )
                # Notify admin for review
                admin = db.execute(select(User).where(User.role == "admin").limit(1)).scalar_one_or_none()
                if admin:
                    create_notification(
                        db,
                        sender_id=user.user_id,
                        recipient_id=admin.user_id,
                        title=f"Thanh toán QR tự động - Phòng {room_code}",
                        message=f"Khách {user.full_name} vừa thanh toán {money(payment.amount)} cho kỳ {payment.period}. Hệ thống đã tự động hoàn tất. Vui lòng kiểm tra ví MoMo.",
                        notification_type="payment"
                    )
                # Notify user
                notify_payment_paid(db, p_updated, confirmed_by_admin=False)
            st.balloons()
            st.success("✅ Thanh toán thành công! Hóa đơn đã được cập nhật.")
            st.info("📱 Admin sẽ kiểm tra ví MoMo và liên hệ nếu có vấn đề.")
            st.session_state.pop("payment_to_pay", None)
            st.rerun()

    else:  # Tiền mặt
        st.info(
            "💡 **Thanh toán tiền mặt:** Vui lòng trực tiếp gặp chủ trọ hoặc người quản lý để nộp tiền.\n\n"
            f"Số tiền cần nộp: **{money(payment.amount)}**  |  Kỳ: **{payment.period}**"
        )


def render_user_management(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Quản lý User", "Quản trị viên theo dõi và xử lý tài khoản (Ban/Khóa).")
    with get_db() as db:
        users = db.execute(select(User).order_by(User.user_id.desc())).scalars().all()
        
    c1, c2 = st.columns([2, 1])
    kw = c1.text_input("Tìm danh sách User")
    role_filter = c2.selectbox("Lọc quyền", ["Tất cả", "admin", "user"])
    
    filtered = []
    for u in users:
        if role_filter != "Tất cả" and u.role != role_filter:
            continue
        if kw and kw.lower() not in (u.username.lower() + " " + u.full_name.lower()):
            continue
        filtered.append(u)
        
    for u in filtered:
        with st.container(border=True):
            cols = st.columns([3, 1])
            with cols[0]:
                st.write(f"**{u.full_name}** (`{u.username}`) - Quyền: {u.role}")
                st.write(f"Email: {u.email or 'N/A'} | SĐT: {u.phone or 'N/A'}")
            with cols[1]:
                new_status = st.selectbox(
                    "Trạng thái",
                    ["active", "locked"],
                    index=["active", "locked"].index(u.status) if u.status in ["active", "locked"] else 0,
                    key=f"status_{u.user_id}"
                )
                if new_status != u.status:
                    if st.button("Lưu thay đổi", key=f"save_{u.user_id}"):
                        with get_db() as write_db:
                            update_user = write_db.get(User, u.user_id)
                            update_user.status = new_status
                            write_db.flush()
                        st.success("Đã cập nhật trạng thái")
                        st.rerun()

def render_user_ai_assistant(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("Trợ lý Người Thuê 🤖", "Hỏi về phòng trọ, hóa đơn, hợp đồng — em trả lời ngay!")

    with get_db() as db:
        tenant = resolve_tenant_for_user(db, user)
        contracts = []
        payments_unpaid = []
        if tenant:
            contracts = db.execute(
                select(Contract)
                .where(Contract.tenant_id == tenant.tenant_id)
                .options(joinedload(Contract.room).joinedload(Room.owner), joinedload(Contract.tenant))
                .order_by(Contract.contract_id.desc())
            ).scalars().all()
            for c in contracts:
                ups = db.execute(
                    select(Payment).where(Payment.contract_id == c.contract_id, Payment.status != "paid")
                ).scalars().all()
                payments_unpaid.extend(ups)
        rooms = db.execute(select(Room).where(Room.status == "available").order_by(Room.room_id.desc())).scalars().all()



        # Prepare context for AI
        tenant_context = "Chưa liên kết"
        if tenant:
            tenant_context = f"Tên: {tenant.full_name}, CMND: {tenant.id_number}, Địa chỉ: {tenant.address}"
        
        contracts_context = "Không có"
        if contracts:
            contracts_context = "\\n".join([
                f"HD#{c.contract_id}: Phòng {room_code_text(c.room)} ({c.room.khu_vuc}), Hạn: {c.start_date.strftime('%d/%m/%Y')} -> {c.end_date.strftime('%d/%m/%Y')}, Giá {c.rent_price}, Tiền cọc {c.deposit}, Trạng thái: {c.status}"
                for c in contracts
            ])
            
        unpaid_context = "Không có"
        if payments_unpaid:
            unpaid_context = "\\n".join([
                f"HD#{p.contract_id} (Kỳ {p.period}): Nợ {p.amount} VNĐ"
                for p in payments_unpaid
            ])

        available_rooms_context = "Hiện tại không có phòng trống nào."
        if rooms:
            parts = []
            for r in rooms[:10]: # Giới hạn 10 phòng
                amenities = []
                if r.has_aircon: amenities.append("Máy lạnh")
                if r.has_fridge: amenities.append("Tủ lạnh")
                if r.has_water_heater: amenities.append("Nóng lạnh")
                if r.has_balcony: amenities.append("Ban công")
                if r.has_elevator: amenities.append("Thang máy")
                tien_ich = ", ".join(amenities) if amenities else "Cơ bản"
                parts.append(f"- Phòng {room_code_text(r)} (Khu {r.khu_vuc}): {float(r.area_m2)}m2, Tầng {r.tang}, Giá thuê: {money(r.current_rent)}, Tiện ích: {tien_ich}")
            available_rooms_context = "\\n".join(parts)

        system_instruction = f"""
Bạn là **Trợ lý AI Người Thuê** của hệ thống quản lý nhà trọ, xưng "Tôi", gọi người dùng là "Quý khách" hoặc "Bạn".

## Dữ liệu cá nhân của Quý khách

**THÔNG TIN NGƯỜI THUÊ:**
{tenant_context}

**HỢP ĐỒNG ĐANG CÓ:**
{contracts_context}

**HÓA ĐƠN CHƯA THANH TOÁN:**
{unpaid_context}

**PHÒNG TRỐNG CÓ THỂ THUÊ:**
{available_rooms_context}

## Nguyên tắc trả lời

1. **Chính xác:** Chỉ sử dụng dữ liệu được cung cấp, không bịa đặt số liệu hợp đồng hay hóa đơn.
2. **Tư vấn phòng:** Khi Quý khách hỏi xem phòng hoặc so sánh phòng, hãy trình bày rõ ràng theo dạng bảng hoặc danh sách: mức giá, diện tích, tầng, tiện ích (máy lạnh, thang máy...).
3. **Hóa đơn:** Nếu Quý khách hỏi về hóa đơn chưa trả, hướng dẫn họ vào mục "Hóa đơn của tôi" để thanh toán qua MoMo QR.
4. **Hợp đồng:** Giải thích nội dung hợp đồng, ngày hết hạn, giá thuê một cách rõ ràng.
5. **Thanh toán:** Hướng dẫn thanh toán qua MoMo QR trong mục "Hóa đơn của tôi". Nếu cần xác nhận thêm, bảo Quý khách liên hệ admin.
6. **Phong cách:** Ngắn gọn, lịch sự, dùng markdown (bold, list, table) cho dễ đọc.
7. **Giới hạn:** Nếu không có dữ liệu hoặc câu hỏi vượt ngoài phạm vi hệ thống, lịch sự hướng dẫn liên hệ admin.
"""

    chat_history = st.session_state.get('user_ai_chat_history', [])

    # First-time greeting
    if not chat_history:
        chat_history.append({"role": "model", "parts": [{"text": (
            "Kính chào Quý khách! Tôi là **Trợ lý Hệ thống** 🏠\n\n"
            "Tôi có thể hỗ trợ Quý khách các tác vụ sau:\n"
            "- Kiểm tra **hợp đồng** đang có hiệu lực\n"
            "- Tra cứu **hóa đơn** chưa thanh toán\n"
            "- Giải đáp thắc mắc về quy trình thuê phòng\n\n"
            "Quý khách cần hỗ trợ nội dung gì hôm nay?"
        )}]})
        st.session_state['user_ai_chat_history'] = chat_history

    for msg in chat_history:
        with st.chat_message("user" if msg["role"] == "user" else "assistant"):
            st.markdown(msg["parts"][0]["text"])

    # Clear old keys if .env key changed
    if 'last_env_key' not in st.session_state:
        st.session_state['last_env_key'] = GEMINI_API_KEY
    elif st.session_state['last_env_key'] != GEMINI_API_KEY and GEMINI_API_KEY:
        # .env key changed, clear cached keys
        st.session_state['user_gemini_key'] = None
        st.session_state['admin_gemini_key'] = None
        st.session_state['last_env_key'] = GEMINI_API_KEY
    
    # API key selection: prefer session > .env
    api_key = st.session_state.get("user_gemini_key") or GEMINI_API_KEY
    
    # Show clear button if using cached key
    if st.session_state.get("user_gemini_key"):
        if st.button("🗑️ Xóa key cũ, dùng key từ .env", key="clear_key"):
            st.session_state['user_gemini_key'] = None
            st.rerun()
    
    if not GENAI_AVAILABLE or not api_key:
        st.error("Vui lòng cài đặt thư viện 'google-genai' và nhập API Key ở menu bên trái để sử dụng chức năng này!")
        st.stop()

    with st.chat_message("user"):
        st.markdown(prompt)
    
    chat_history.append({"role": "user", "parts": [{"text": prompt}]})
    
    with st.chat_message("assistant"):
        with st.spinner("Đang suy nghĩ..."):
                try:
                    client = genai.Client(api_key=api_key)
                    
                    history_for_api = []
                    for m in chat_history[:-1]:
                        history_for_api.append(types.Content(role=m["role"], parts=[types.Part(text=m["parts"][0]["text"])]))

                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=history_for_api + [prompt],
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.3
                        ),
                    )
                    
                    answer = response.text
                    st.markdown(answer)
                    chat_history.append({"role": "model", "parts": [{"text": answer}]})
                    st.session_state['user_ai_chat_history'] = chat_history
                    
                except Exception as e:
                    error_msg = str(e)
                    if "403" in error_msg or "PERMISSION_DENIED" in error_msg or "leaked" in error_msg.lower():
                        st.error("""
                        🔑 **API Key đã bị khóa!**
                        
                        API Key hiện tại đã bị Google đánh dấu là leaked. Vui lòng:
                        
                        1. Truy cập: https://aistudio.google.com/app/apikey
                        2. Tạo API key mới
                        3. Nhập key mới ở thanh bên trái (phần Cấu hình AI)
                        
                        Hoặc nhập trực tiếp API key mới bên dưới:
                        """)
                        new_key = st.text_input("API Key mới", type="password", key="new_gemini_key_input")
                        if new_key and st.button("Cập nhật API Key", key="update_key_btn"):
                            st.session_state['user_gemini_key'] = new_key
                            st.success("✅ Đã cập nhật API Key! Vui lòng gửi lại câu hỏi.")
                            st.rerun()
                    elif "400" in error_msg or "expired" in error_msg.lower() or "INVALID_ARGUMENT" in error_msg:
                        st.error("""
                        ⏰ **API Key đã hết hạn!**
                        
                        API Key hiện tại đã hết hạn hoặc không hợp lệ. Vui lòng:
                        
                        1. Truy cập: https://aistudio.google.com/app/apikey
                        2. Tạo API key mới
                        3. Nhập key mới ở thanh bên trái (phần Cấu hình AI)
                        
                        Hoặc nhập trực tiếp API key mới bên dưới:
                        """)
                        new_key = st.text_input("API Key mới", type="password", key="new_key_expired")
                        if new_key and st.button("Cập nhật API Key", key="update_btn_expired"):
                            st.session_state['user_gemini_key'] = new_key
                            st.success("✅ Đã cập nhật API Key! Vui lòng gửi lại câu hỏi.")
                            st.rerun()
                    else:
                        st.error(f"Lỗi khi gọi API: {error_msg}")

    if st.button("🗑️ Xóa lịch sử chat"):
        st.session_state['user_ai_chat_history'] = []
        st.rerun()



def render_ai_agent(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("AI Agent Vận Hành 🤖", "Trợ lý AI chuyên biệt: truy vấn DB, phân tích rủi ro, tối ưu giá và tóm tắt vận hành.")

    with get_db() as db:
        rooms = db.execute(select(Room)).scalars().all()
        total_rooms = len(rooms)
        occupied_rooms = sum(1 for r in rooms if r.status == "occupied")
        available_rooms = total_rooms - occupied_rooms
        tenants = db.execute(select(Tenant)).scalars().all()
        unpaid_payments = db.execute(select(Payment).where(Payment.status != "paid")).scalars().all()
        unpaid_total = sum(p.amount for p in unpaid_payments)
        paid_total = db.execute(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "paid")).scalar() or 0
        contracts = db.execute(select(Contract).where(Contract.status == "active")).scalars().all()

    # Admin AI overview metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Phòng trống", available_rooms, f"/{total_rooms}")
    m2.metric("Hợp đồng active", len(contracts))
    m3.metric("Công nợ chưa thu", money(unpaid_total))
    m4.metric("Doanh thu đã thu", money(paid_total))

    tabs = st.tabs(["💬 Chat AI Agent", "⚡ Tác vụ Tự động", "📊 Báo cáo nhanh"])

    with tabs[0]:
        with st.sidebar:
            st.markdown("---")
            st.markdown("### 🔑 Cấu hình AI Admin")
            api_key_input = st.text_input("Gemini API Key", value=st.session_state.get('admin_gemini_key', GEMINI_API_KEY), type="password", key="ai_key_admin")
            if api_key_input:
                st.session_state['admin_gemini_key'] = api_key_input

        # Prepare context summary
        avail_rooms = [r for r in rooms if r.status == 'available']
        avails_str = ", ".join([room_code_text(r) for i, r in enumerate(avail_rooms) if i < 15])
        unpaid_sliced = [p for i, p in enumerate(unpaid_payments) if i < 15]
        debt_details = "\\n".join([f"HD#{p.contract_id} (Kỳ {p.period}): {money(p.amount)}" for p in unpaid_sliced])
        
        system_instruction = f"""
Bạn là **AI Agent Vận Hành** chuyên nghiệp, hỗ trợ Admin quản lý hệ thống nhà trọ toàn diện. Xưng "tôi", gọi "Admin".

## Dữ liệu hệ thống hiện tại

**TỔNG QUAN:**
- Tổng phòng: **{total_rooms}** (Trống: {available_rooms} | Đang thuê: {occupied_rooms})
- Tỷ lệ lấp đầy: **{round(occupied_rooms/total_rooms*100) if total_rooms else 0}%**
- Phòng trống hiện có: {avails_str if avails_str else 'Không có'}
- Hợp đồng active: **{len(contracts)}**
- Công nợ chưa thu: **{len(unpaid_payments)} hóa đơn** — Tổng **{money(unpaid_total)}**
- Doanh thu đã thu: **{money(paid_total)}**

**CHI TIẾT CÔNG NỢ (TOP 15):**
{debt_details if debt_details else 'Không có công nợ'}

## Nguyên tắc hỗ trợ

1. **Phân tích chính xác:** Dùng đúng số liệu trên, không bịa.
2. **Đề xuất hành động:** Sau mỗi phân tích, luôn đề xuất ít nhất 1-2 hành động quản trị cụ thể (nhắc nợ, điều chỉnh giá, gia hạn hợp đồng...).
3. **Cảnh báo chủ động:** Nếu công nợ cao hoặc nhiều phòng trống, hãy tự động cảnh báo và gợi ý giải pháp.
4. **Format rõ ràng:** Dùng **bold**, table, bullet list cho dễ đọc. Số tiền luôn format VNĐ.
5. **Báo cáo nhanh:** Khi Admin hỏi tổng quan, cung cấp tóm tắt dashboard ngắn gọn + highlight vấn đề nổi bật.
6. **Dự báo:** Nếu có dữ liệu hợp đồng sắp hết hạn hoặc nợ tồn đọng, cảnh báo rủi ro doanh thu.
"""

        chat_history = st.session_state.get('admin_ai_chat_history', [])
        if not chat_history:
            chat_history.append({"role": "model", "parts": [{"text": (
                "Chào Admin! Tôi là **AI Agent Vận Hành** chuyên sâu."
                "\n\nTôi vừa đọc các số liệu kinh doanh: **công nợ**, **doanh thu**, và **tình trạng phòng trống**."
                "\n\nBạn cần phân tích báo cáo hay tư vấn gì hôm nay?"
            )}]})
            st.session_state['admin_ai_chat_history'] = chat_history

        for msg in chat_history:
            with st.chat_message("user" if msg["role"] == "user" else "assistant"):
                st.markdown(msg["parts"][0]["text"])

        if prompt := st.chat_input("Hỏi AI Agent về dữ liệu hệ thống (VD: Phòng trống, doanh thu, công nợ)..."):
            api_key = st.session_state.get("admin_gemini_key", GEMINI_API_KEY)
            if not GENAI_AVAILABLE or not api_key:
                st.error("Vui lòng cài đặt thư viện 'google-genai' và nhập API Key ở menu bên trái để sử dụng chức năng này!")
                st.stop()

            chat_history.append({"role": "user", "parts": [{"text": prompt}]})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.spinner("Đang chạy phân tích..."):
                    try:
                        client = genai.Client(api_key=api_key)
                        
                        history_for_api = []
                        for m in chat_history[:-1]:
                            history_for_api.append(types.Content(role=m["role"], parts=[types.Part(text=m["parts"][0]["text"])]))

                        response = client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=history_for_api + [prompt],
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.1
                            ),
                        )
                        
                        answer = response.text
                        st.markdown(answer)
                        chat_history.append({"role": "model", "parts": [{"text": answer}]})
                        st.session_state['admin_ai_chat_history'] = chat_history
                        
                    except Exception as e:
                        st.error(f"Lỗi khi gọi API: {str(e)}")

        if st.button("🗑️ Xóa lịch sử chat"):
            st.session_state['admin_ai_chat_history'] = []
            st.rerun()

    with tabs[1]:
        st.markdown("⚡ **Tác Vụ Chuyên Môn (Smart Tools)**")
        c1, c2, c3 = st.columns(3)
        if c1.button("🔍 Phân tích Rủi ro Công Nợ", use_container_width=True):
            if unpaid_payments:
                st.warning(f"**AI Scan:** Rủi ro công nợ = **{money(unpaid_total)}** ({len(unpaid_payments)} hóa đơn). Đề xuất ưu tiên xử lý các hóa đơn quá 10 ngày.")
                overdue = [p for p in unpaid_payments if p.paid_date is None]
                if overdue:
                    df_debt = pd.DataFrame([{"HĐ#": p.contract_id, "Kỳ": p.period, "Số tiền": float(p.amount)} for p in overdue[:10]])
                    st.dataframe(df_debt, use_container_width=True, hide_index=True)
            else:
                st.success("Không có công nợ. Tất cả hóa đơn đã được thanh toán!")

        if c2.button("💰 Gợi ý Tối Ưu Hóa Giá", use_container_width=True):
            avails = [r for r in rooms if r.status == "available"]
            if avails:
                results = []
                for r in avails[:5]:
                    sug, bk = calculate_price_for_room(r)
                    diff = float(sug) - float(r.current_rent)
                    results.append({"Phòng": room_code_text(r), "Giá hiện tại": float(r.current_rent), "AI Gợi ý": float(sug), "Chênh lệch": diff})
                st.success("**AI Pricing:** Phân tích giá cho các phòng trống")
                st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
            else:
                st.info("Tất cả các phòng đang được thuê - Công suất 100%! 🎉")

        if c3.button("📋 Hợp đồng Sắp Hết Hạn", use_container_width=True):
            today = date.today()
            expiring = [c for c in contracts if c.end_date and (c.end_date - today).days <= 30]
            if expiring:
                st.warning(f"**{len(expiring)} hợp đồng** sắp hết hạn trong 30 ngày tới!")
                df_exp = pd.DataFrame([{
                    "HD#": c.contract_id,
                    "Phòng": room_code_text(c.room) if c.room else "",
                    "Ngày hết hạn": c.end_date.strftime('%d/%m/%Y'),
                    "Còn lại (ngày)": (c.end_date - today).days
                } for c in expiring])
                st.dataframe(df_exp, use_container_width=True, hide_index=True)
            else:
                st.success("Không có hợp đồng nào sắp hết hạn trong 30 ngày!")

    with tabs[2]:
        st.markdown("📊 **Báo cáo nhanh toàn hệ thống**")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Tình trạng phòng")
            st.bar_chart(pd.DataFrame([
                {"Trạng thái": "Trống", "Số lượng": available_rooms},
                {"Trạng thái": "Đang thuê", "Số lượng": occupied_rooms}
            ]).set_index("Trạng thái"))
        with col2:
            st.markdown("##### Tài chính tổng hợp")
            st.bar_chart(pd.DataFrame([
                {"Loại": "Đã thu", "Số tiền": float(paid_total)},
                {"Loại": "Chưa thu", "Số tiền": float(unpaid_total)}
            ]).set_index("Loại"))


def main() -> None:
    inject_global_styles()
    try:
        init_database()
    except Exception as ex:
        st.error(f"Không kết nối được cơ sở dữ liệu: {ex}")
        st.stop()

    user = current_user()
    if not user:
        render_auth_screen()
        return

    # Hiển thị banner thông báo thanh toán cho user thường
    if user.role != "admin":
        render_payment_notification_banner(user)

    selected = render_sidebar_with_notifications(user)
    if user.role == "admin":
        if selected == "Dashboard":
            render_dashboard()
        elif selected == "Quản lý phòng":
            render_rooms(user)
        elif selected == "Quản lý người thuê":
            render_tenants(user)
        elif selected == "Quản lý hợp đồng":
            render_contracts(user)
        elif selected == "Quản lý hóa đơn":
            render_payments(user)
        elif selected == "Gợi ý giá thuê":
            render_price_suggestion(user)
        elif selected == "Audit Log":
            render_audit_logs()
        elif selected == "Quản lý User":
            render_user_management(user)
        elif selected in ("📨 Trung tâm Thông báo", "Trung tâm Thông báo"):
            render_admin_notifications(user)
        elif selected == "Trợ lý AI":
            render_ai_agent(user)
    else:
        # Note: Inline QR payment is now handled in render_user_payments
        if selected == "Danh sách phòng":
            render_user_room_catalog(user)
        elif selected == "Gợi ý giá thuê":
            render_user_price_suggestion(user)
        elif selected == "Hợp đồng của tôi":
            render_user_contracts(user)
        elif selected == "Hóa đơn của tôi":
            render_user_payments(user)
        elif selected in ("🔔 Thông báo của tôi", "Thông báo của tôi"):
            render_user_notifications(user)
        elif selected == "Trợ lý AI":
            render_user_ai_assistant(user)


def render_admin_notifications(user: SessionUser) -> None:
    """UI quản lý thông báo cho Admin - gửi và quản lý"""
    hero(APP_NAME)
    section_header("📨 Trung tâm Thông báo", "Gửi thông báo đến người thuê và quản lý hệ thống thông báo.")

    tabs = st.tabs(["📤 Gửi thông báo", "📥 Tin nhắn từ User", "⚙️ Thông báo hệ thống", "🤖 Tự động hóa AI"])

    with get_db() as db:
        all_users = db.execute(select(User).where(User.role == "user").order_by(User.full_name)).scalars().all()
        tenants = db.execute(
            select(Tenant)
            .options(
                joinedload(Tenant.user),
                joinedload(Tenant.contracts).joinedload(Contract.room)
            )
            .order_by(Tenant.full_name)
        ).unique().scalars().all()

    with tabs[0]:
        st.markdown("### 📤 Gửi thông báo đến người thuê")

        # User selection
        user_options = {"Tất cả người thuê": "all"}
        for u in all_users:
            # Find tenant info if linked
            tenant_info = next((t for t in tenants if t.user_id == u.user_id), None)
            label = f"{u.full_name} ({u.username})"
            if tenant_info:
                room_code = "Chưa thuê"
                if tenant_info.contracts:
                    active_contract = next((c for c in tenant_info.contracts if c.status == "active"), None)
                    if active_contract and active_contract.room:
                        room_code = active_contract.room.room_code
                    else:
                        room_code = tenant_info.contracts[0].room.room_code if tenant_info.contracts[0].room else "Chưa thuê"
                label += f" - Phòng: {room_code}"
            user_options[label] = u.user_id

        selected_user = st.selectbox("Chọn người nhận", list(user_options.keys()))

        col1, col2 = st.columns(2)
        with col1:
            title = st.text_input("Tiêu đề thông báo", placeholder="VD: Thông báo thu tiền trễ...")
        with col2:
            notification_type = st.selectbox(
                "Loại thông báo",
                ["general", "payment", "contract", "maintenance"],
                format_func=lambda x: {
                    "general": "📢 Chung",
                    "payment": "💰 Thanh toán",
                    "contract": "📄 Hợp đồng",
                    "maintenance": "🔧 Bảo trì",
                }.get(x, x),
            )

        message = st.text_area("Nội dung thông báo", placeholder="Nhập nội dung chi tiết...", height=120)

        if st.button("📨 Gửi thông báo", type="primary", disabled=not (title and message)):
            with get_db() as db:
                target_id = user_options[selected_user]
                if target_id == "all":
                    # Send to all users
                    sent_count = 0
                    for u in all_users:
                        send_admin_to_user_message(
                            db,
                            user.user_id,
                            u.user_id,
                            title,
                            message,
                            notification_type,
                        )
                        sent_count += 1
                    st.success(f"Đã gửi thông báo đến {sent_count} người dùng!")
                else:
                    send_admin_to_user_message(
                        db,
                        user.user_id,
                        target_id,
                        title,
                        message,
                        notification_type,
                    )
                    st.success(f"Đã gửi thông báo đến {selected_user}!")

        st.markdown("---")
        st.markdown("#### 📋 Mẫu thông báo nhanh")
        col1, col2, col3 = st.columns(3)
        if col1.button("💰 Nhắc thanh toán", use_container_width=True):
            st.session_state["notif_template"] = {
                "title": "⏰ Nhắc nhở thanh toán",
                "message": "Bạn có hóa đơn chưa thanh toán. Vui lòng thanh toán trước ngày 5 để tránh phí phạt.",
                "type": "payment",
            }
            st.rerun()
        if col2.button("🔧 Bảo trì", use_container_width=True):
            st.session_state["notif_template"] = {
                "title": "🔧 Thông báo bảo trì",
                "message": "Hệ thống sẽ tiến hành bảo trì định kỳ vào ngày mai. Mong quý khách thông cảm.",
                "type": "maintenance",
            }
            st.rerun()
        if col3.button("📄 Hợp đồng", use_container_width=True):
            st.session_state["notif_template"] = {
                "title": "📄 Nhắc hợp đồng sắp hết hạn",
                "message": "Hợp đồng thuê phòng của bạn sắp hết hạn. Vui lòng liên hệ để gia hạn.",
                "type": "contract",
            }
            st.rerun()

        # Fill template if exists
        if st.session_state.get("notif_template"):
            template = st.session_state.pop("notif_template")
            st.info(f"Đã chọn mẫu: {template['title']}")

    with tabs[1]:
        st.markdown("### 📥 Tin nhắn từ người thuê")
        with get_db() as db:
            user_messages = db.execute(
                select(Notification)
                .options(joinedload(Notification.sender))
                .where(
                    Notification.recipient_id == user.user_id,
                    Notification.is_system == False,
                )
                .order_by(Notification.created_at.desc())
                .limit(50)
            ).scalars().all()

        if user_messages:
            for msg in user_messages:
                with st.container(border=True):
                    col1, col2, col3 = st.columns([3, 1, 1])
                    sender_name = msg.sender.full_name if msg.sender else "Unknown"
                    col1.markdown(f"**📧 {msg.title}**")
                    col1.caption(f"Từ: {sender_name} • {msg.created_at.strftime('%d/%m/%Y %H:%M')}")
                    col2.markdown(f"*{msg.notification_type}*")
                    if not msg.is_read:
                        col3.markdown(":red[🔴 Chưa đọc]")
                    st.markdown(f"{msg.message}")

                    if not msg.is_read:
                        if st.button("✅ Đánh dấu đã đọc", key=f"read_{msg.notification_id}"):
                            mark_as_read(db, msg.notification_id, user.user_id)
                            st.rerun()
        else:
            st.info("Chưa có tin nhắn từ người thuê")

    with tabs[2]:
        st.markdown("### ⚙️ Thông báo hệ thống")
        with get_db() as db:
            system_notifs = db.execute(
                select(Notification)
                .where(
                    Notification.recipient_id == user.user_id,
                    Notification.is_system == True,
                )
                .order_by(Notification.created_at.desc())
                .limit(50)
            ).scalars().all()

        if system_notifs:
            for notif in system_notifs:
                icon = {
                    "payment": "💰",
                    "contract": "📄",
                    "reminder": "⏰",
                    "maintenance": "🔧",
                    "general": "📢",
                }.get(notif.notification_type, "📢")

                with st.container(border=True):
                    col1, col2 = st.columns([4, 1])
                    col1.markdown(f"**{icon} {notif.title}**")
                    col1.caption(f"{notif.created_at.strftime('%d/%m/%Y %H:%M')}")
                    col2.markdown(":green[✅ Đã đọc]" if notif.is_read else ":red[🔴 Chưa đọc]")
                    st.markdown(notif.message)
        else:
            st.info("Chưa có thông báo hệ thống")

    with tabs[3]:
        render_ai_automation_dashboard(user)


def render_user_notifications(user: SessionUser) -> None:
    """UI thông báo cho User - nhận và xem"""
    hero(APP_NAME)
    section_header("🔔 Thông báo của tôi", "Xem thông báo từ Admin và hệ thống.")

    with get_db() as db:
        unread_count = get_unread_count(db, user.user_id)
        notifications = get_user_notifications(db, user.user_id, limit=50)

    # Summary
    col1, col2, col3 = st.columns(3)
    col1.metric("Tổng thông báo", len(notifications))
    col2.metric("Chưa đọc", unread_count, delta=f"{unread_count} mới" if unread_count > 0 else None)

    if unread_count > 0:
        if col3.button("✅ Đánh dấu tất cả đã đọc", use_container_width=True):
            with get_db() as db:
                mark_all_as_read(db, user.user_id)
            st.success(f"Đã đánh dấu {unread_count} thông báo là đã đọc!")
            st.rerun()

    st.markdown("---")

    # Filter
    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        show_only = st.selectbox("Lọc thông báo", ["Tất cả", "Chưa đọc", "Hệ thống", "Từ Admin"])
    with filter_col2:
        if st.button("🗑️ Xóa tất cả đã đọc", use_container_width=True):
            st.info("Chức năng xóa sẽ được triển khai sau")

    st.markdown("---")

    # Display notifications
    filtered = notifications
    if show_only == "Chưa đọc":
        filtered = [n for n in notifications if not n.is_read]
    elif show_only == "Hệ thống":
        filtered = [n for n in notifications if n.is_system]
    elif show_only == "Từ Admin":
        filtered = [n for n in notifications if not n.is_system and n.sender_id is not None]

    if not filtered:
        st.info("Không có thông báo nào phù hợp")
        return

    for notif in filtered:
        icon_map = {
            "payment": "💰",
            "contract": "📄",
            "reminder": "⏰",
            "maintenance": "🔧",
            "general": "📢",
            "system": "⚙️",
        }
        icon = icon_map.get(notif.notification_type, "📢")

        bg_color = "#fef3c7" if not notif.is_read else "#f3f4f6"
        border_color = "#f59e0b" if not notif.is_read else "#d1d5db"

        with st.container(border=True):
            st.markdown(
                f"""
                <div style="background: {bg_color}; border-left: 4px solid {border_color}; padding: 12px; border-radius: 8px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 600; font-size: 1.05rem;">{icon} {notif.title}</span>
                        <span style="font-size: 0.85rem; color: #6b7280;">{notif.created_at.strftime('%d/%m/%Y %H:%M')}</span>
                    </div>
                    <div style="margin-top: 8px; color: #374151;">{notif.message}</div>
                    <div style="margin-top: 8px; font-size: 0.8rem;">
                        {f"<span style='color: #ef4444;'>🔴 Chưa đọc</span>" if not notif.is_read else "<span style='color: #22c55e;'>✅ Đã đọc</span>"}
                        {f" • Từ: <b>{notif.sender.full_name}</b>" if notif.sender else " • Từ: Hệ thống"}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if not notif.is_read:
                if st.button("✅ Đánh dấu đã đọc", key=f"user_read_{notif.notification_id}", use_container_width=True):
                    with get_db() as db:
                        mark_as_read(db, notif.notification_id, user.user_id)
                    st.rerun()


def render_ai_automation_dashboard(user: SessionUser) -> None:
    """Dashboard tự động hóa AI cho Admin"""
    st.markdown("### 🤖 Trung tâm Tự động hóa AI")
    st.caption("AI Agent tự động phát hiện và đề xuất các tác vụ quản lý")

    with get_db() as db:
        ai_service = AIAutomationService(db)
        all_tasks = ai_service.run_all_automation_checks()

    # Summary metrics
    total_tasks = sum(len(tasks) for tasks in all_tasks.values())
    high_priority = sum(1 for tasks in all_tasks.values() for t in tasks if t.priority == "high")
    auto_executable = sum(1 for tasks in all_tasks.values() for t in tasks if t.auto_executable)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📊 Tổng tác vụ", total_tasks)
    col2.metric("🔴 Ưu tiên cao", high_priority, delta=f"{high_priority} cần xử lý" if high_priority > 0 else None)
    col3.metric("⚡ Tự động được", auto_executable)
    avg_confidence = sum(float(t.confidence_score) for tasks in all_tasks.values() for t in tasks) / max(total_tasks, 1)
    col4.metric("🎯 Tự tin trung bình", f"{avg_confidence:.0%}")

    st.markdown("---")

    # Auto-run automation for high confidence tasks
    if st.button("🚀 Chạy tự động các tác vụ đủ tin cậy", type="primary"):
        executed = 0
        with get_db() as db:
            for category, tasks in all_tasks.items():
                for task in tasks:
                    if task.auto_executable and task.confidence_score >= 0.85:
                        if task.task_type == "payment_overdue" and task.related_entity_id:
                            # Send reminder notification
                            payment = db.get(Payment, task.related_entity_id)
                            if payment:
                                notify_payment_reminder(db, payment, days_until_due=-5)
                                executed += 1
                        elif task.task_type == "contract_ending" and task.related_entity_id:
                            contract = db.get(Contract, task.related_entity_id)
                            if contract:
                                from services.notification_service import notify_contract_ending_soon
                                notify_contract_ending_soon(db, contract, 30)
                                executed += 1

        if executed > 0:
            st.success(f"✅ Đã tự động thực hiện {executed} tác vụ!")
        else:
            st.info("Không có tác vụ nào đủ điều kiện tự động thực hiện")

    st.markdown("---")

    # Display tasks by category
    tabs = st.tabs(["💰 Thanh toán", "📄 Hợp đồng", "🏠 Phòng", "💰 Giá thuê"])

    category_map = {
        0: ("payment", "💰 Thanh toán"),
        1: ("contract", "📄 Hợp đồng"),
        2: ("room", "🏠 Phòng"),
        3: ("pricing", "💰 Giá thuê"),
    }

    for idx, (tab, (cat_key, cat_name)) in enumerate(zip(tabs, category_map.items())):
        with tab:
            tasks = all_tasks.get(cat_key, [])
            if not tasks:
                st.success(f"✅ Không có vấn đề nào trong mục {cat_name}")
                continue

            for task in tasks:
                priority_color = {"high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}.get(task.priority, "#6b7280")
                priority_text = {"high": "🔴 Cao", "medium": "🟡 Trung bình", "low": "🟢 Thấp"}.get(task.priority, "⚪")

                with st.container(border=True):
                    col1, col2, col3 = st.columns([3, 1, 1])
                    col1.markdown(f"**{task.title}**")
                    col1.caption(f"{task.description[:150]}...")
                    col2.markdown(f"<span style='color: {priority_color};'>{priority_text}</span>", unsafe_allow_html=True)
                    col3.progress(task.confidence_score, text=f"Tự tin: {task.confidence_score:.0%}")

                    if task.auto_executable:
                        st.success(f"⚡ Có thể tự động: {task.suggested_action}")
                    else:
                        st.warning(f"👤 Cần xem xét: {task.suggested_action}")


def get_notification_badge_html(count: int) -> str:
    """Generate notification badge HTML"""
    if count == 0:
        return ""
    return f"""<span style="
        background-color: #ef4444;
        color: white;
        border-radius: 50%;
        padding: 2px 6px;
        font-size: 0.7rem;
        margin-left: 4px;
        font-weight: bold;
    ">{count}</span>"""


def render_sidebar_with_notifications(user: SessionUser) -> str:
    """Sidebar with notification badges, icons, and collapsible sections"""
    with st.sidebar:
        with get_db() as db:
            unread_count = get_unread_count(db, user.user_id) if user.role != "admin" else 0

        if user.role == "admin":
            menu_groups = {
                "QUẢN LÝ": [
                    ("🏠", "Dashboard", "Tổng quan hệ thống"),
                    ("🚪", "Quản lý phòng", "Phòng trọ & hình ảnh"),
                    ("👥", "Quản lý người thuê", "Hồ sơ người thuê"),
                    ("📄", "Quản lý hợp đồng", "Hợp đồng thuê"),
                    ("💳", "Quản lý hóa đơn", "Thanh toán & hóa đơn"),
                ],
                "CÔNG CỤ": [
                    ("💡", "Gợi ý giá thuê", "AI gợi ý giá"),
                    ("📋", "Audit Log", "Lịch sử thay đổi"),
                    ("👤", "Quản lý User", "Tài khoản người dùng"),
                    ("📨", "📨 Trung tâm Thông báo", "Gửi & quản lý thông báo"),
                    ("🤖", "Trợ lý AI", "AI Agent vận hành"),
                ],
            }
            flat_options = [item[1] for group in menu_groups.values() for item in group]
        else:
            badge = f" ({unread_count})" if unread_count > 0 else ""
            menu_groups = {
                "TIỆN ÍCH": [
                    ("🏠", "Danh sách phòng", "Xem phòng cho thuê"),
                    ("💡", "Gợi ý giá thuê", "AI gợi ý giá thuê"),
                    ("📄", "Hợp đồng của tôi", "Hợp đồng đang có"),
                    ("💳", "Hóa đơn của tôi", "Thanh toán MoMo"),
                    ("🔔", f"🔔 Thông báo của tôi", f"Thông báo{badge}"),
                    ("🤖", "Trợ lý AI", "Hỏi đáp tự động"),
                ],
            }
            flat_options = [item[1] for group in menu_groups.values() for item in group]

        # Use session state for menu selection and collapse state
        if "selected_menu" not in st.session_state:
            st.session_state.selected_menu = flat_options[0]
        if "sidebar_collapsed" not in st.session_state:
            st.session_state.sidebar_collapsed = False

        # Custom CSS
        st.markdown("""
        <style>
        /* Sidebar nav buttons — override Streamlit emotion CSS */
        [data-testid="stSidebar"] .stButton > button {
            width: 100% !important;
            text-align: left !important;
            background-color: rgba(30, 41, 59, 0.85) !important;
            border: 1px solid rgba(255,255,255,0.12) !important;
            color: #e2e8f0 !important;
            padding: 0.5rem 0.85rem !important;
            margin: 0.1rem 0 !important;
            border-radius: 8px !important;
            font-weight: 500 !important;
            font-size: 0.88rem !important;
            transition: all 0.18s !important;
            justify-content: flex-start !important;
        }
        /* Override inner div that Streamlit renders inside button */
        [data-testid="stSidebar"] .stButton > button > div,
        [data-testid="stSidebar"] .stButton > button > div > div {
            background: transparent !important;
            background-color: transparent !important;
        }
        /* Text inside button */
        [data-testid="stSidebar"] .stButton > button p,
        [data-testid="stSidebar"] .stButton > button span,
        [data-testid="stSidebar"] .stButton > button div {
            color: #e2e8f0 !important;
            background: transparent !important;
            font-size: 0.88rem !important;
        }
        /* Hover state */
        [data-testid="stSidebar"] .stButton > button:hover {
            background-color: rgba(59, 130, 246, 0.25) !important;
            border-color: rgba(59, 130, 246, 0.5) !important;
            color: #ffffff !important;
        }
        [data-testid="stSidebar"] .stButton > button:hover p,
        [data-testid="stSidebar"] .stButton > button:hover span,
        [data-testid="stSidebar"] .stButton > button:hover div {
            color: #ffffff !important;
        }
        /* Active/primary button */
        [data-testid="stSidebar"] .stButton > button[kind="primary"],
        [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {
            background-color: #3b82f6 !important;
            border-color: #2563eb !important;
            color: #ffffff !important;
            font-weight: 600 !important;
        }
        [data-testid="stSidebar"] .stButton > button[kind="primary"] p,
        [data-testid="stSidebar"] .stButton > button[kind="primary"] span,
        [data-testid="stSidebar"] .stButton > button[kind="primary"] div,
        [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] p,
        [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] div {
            color: #ffffff !important;
            background: transparent !important;
            font-weight: 600 !important;
        }
        /* Group label */
        .sidebar-group-label {
            color: #64748b;
            font-size: 0.7rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            padding: 0.6rem 0.5rem 0.2rem 0.5rem;
            margin-top: 0.4rem;
        }
        </style>
        """, unsafe_allow_html=True)

        # App title + collapse toggle
        col_title, col_toggle = st.columns([3, 1])
        with col_title:
            st.markdown("<div style='font-weight:700;font-size:1rem;color:#e2e8f0;padding:0.3rem 0;'>Chức năng</div>", unsafe_allow_html=True)
        with col_toggle:
            toggle_label = "▲" if not st.session_state.sidebar_collapsed else "▼"
            if st.button(toggle_label, key="toggle_sidebar", help="Thu gọn / Mở rộng menu"):
                st.session_state.sidebar_collapsed = not st.session_state.sidebar_collapsed
                st.rerun()

        if not st.session_state.sidebar_collapsed:
            for group_name, items in menu_groups.items():
                st.markdown(f"<div class='sidebar-group-label'>{group_name}</div>", unsafe_allow_html=True)
                for icon, option, desc in items:
                    # Normalize key: strip emoji prefix if option already has it
                    clean_key = option.replace("📨 ", "").replace("🔔 ", "")
                    is_active = st.session_state.selected_menu == option or st.session_state.selected_menu == clean_key
                    btn_type = "primary" if is_active else "secondary"
                    label = f"{icon} {option}" if not option.startswith(icon) else option
                    # Add unread badge inline for notifications
                    if "Thông báo" in option and unread_count > 0 and user.role != "admin":
                        label = f"{icon} Thông báo của tôi  🔴{unread_count}"
                    if st.button(label, key=f"nav_{clean_key}", use_container_width=True, type=btn_type, help=desc):
                        st.session_state.selected_menu = option
                        st.rerun()
        else:
            st.markdown("<div style='color:#94a3b8;font-size:0.8rem;padding:0.5rem;'>Menu đã thu gọn</div>", unsafe_allow_html=True)
            if st.button("▼ Mở menu", key="expand_menu", use_container_width=True):
                st.session_state.sidebar_collapsed = False
                st.rerun()

        st.markdown("---")
        role_label = "Quản trị viên" if user.role == "admin" else "Người thuê"
        st.markdown(
            f"<div class='muted-box'><b>{user.full_name}</b><br><span style='font-size:0.8rem;color:#94a3b8;'>{user.username} • {role_label}</span></div>",
            unsafe_allow_html=True,
        )
        if st.button("🚪 Đăng xuất", use_container_width=True):
            logout()
            st.rerun()

    # Normalize selected_menu for routing
    selected = st.session_state.selected_menu
    # Strip emoji prefix variants for consistent routing
    if selected.startswith("📨 "):
        selected = selected[3:]
    return selected


def inject_global_styles() -> None:
    st.markdown(
        """
<style>
    :root {
        --bg-page: #f8fafc;
        --bg-card: #ffffff;
        --border-soft: #e2e8f0;
        --text-main: #111827;
        --text-muted: #64748b;
        --accent: #ff5b57;
        --accent-dark: #ef4444;
        --warn-bg: #fff7e6;
        --warn-border: #f5c46b;
        --info-bg: #eff6ff;
        --info-border: #bfdbfe;
    }

    .stApp {
        background: var(--bg-page);
        color: var(--text-main);
    }

    [data-testid="stAppViewContainer"] {
        background: linear-gradient(180deg, #fcfcfd 0%, #f8fafc 100%);
    }

    [data-testid="stMainBlockContainer"] {
        max-width: 1320px;
        padding-top: 1.1rem;
    }

    .hero {
        margin: 0.2rem 0 0.35rem;
    }

    .hero h1 {
        margin: 0;
        font-size: 2.15rem;
        line-height: 1.1;
        font-weight: 800;
        color: #171717;
        letter-spacing: -0.02em;
    }

    .card-shell {
        background: transparent;
        border: 0;
        padding: 0;
        margin-bottom: 0.85rem;
    }

    .section-title {
        color: #334155;
        font-size: 0.95rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
    }

    .section-desc {
        color: var(--text-muted);
        font-size: 0.9rem;
        margin-bottom: 0.2rem;
    }

    .muted-box {
        background: #ffffff;
        border: 1px solid var(--border-soft);
        border-radius: 12px;
        padding: 0.85rem 0.95rem;
        color: #0f172a;
    }

    [data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid var(--border-soft);
        border-radius: 12px;
        padding: 0.9rem 1rem;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }

    [data-testid="stMetricLabel"] {
        color: var(--text-muted);
        font-weight: 600;
    }

    [data-testid="stMetricValue"] {
        color: #111827;
        font-weight: 800;
    }

    [data-testid="stSidebar"] {
        background: #f8fafc;
        border-right: 1px solid #e5e7eb;
    }
</style>
        """,
        unsafe_allow_html=True,
    )


def render_payment_notification_banner(user: SessionUser) -> None:
    with get_db() as db:
        tenant = resolve_tenant_for_user(db, user)
        if not tenant:
            return
        unpaid_payments = db.execute(
            select(Payment)
            .join(Payment.contract)
            .where(Contract.tenant_id == tenant.tenant_id, Payment.status == "unpaid")
            .options(joinedload(Payment.contract).joinedload(Contract.room))
            .order_by(Payment.period.asc())
        ).scalars().all()
        overdue_count = db.scalar(
            select(func.count(Payment.payment_id))
            .join(Payment.contract)
            .where(Contract.tenant_id == tenant.tenant_id, Payment.status == "overdue")
        ) or 0

    if not unpaid_payments and overdue_count == 0:
        return

    total_unpaid = sum(p.amount for p in unpaid_payments)
    bills_count = len(unpaid_payments) + overdue_count
    first_unpaid = unpaid_payments[0] if unpaid_payments else None
    tone_bg = "#fff7e6" if overdue_count == 0 else "#fff1f2"
    tone_border = "#f5c46b" if overdue_count == 0 else "#fca5a5"
    icon = "⚠️" if overdue_count == 0 else "🚨"

    st.markdown(
        f"""
        <div style="
            background:{tone_bg};
            border:1px solid {tone_border};
            border-radius:12px;
            padding:0.9rem 1rem;
            margin-bottom:0.7rem;
        ">
            <div style="font-weight:700;color:#92400e;font-size:0.96rem;">
                {icon} Bạn có <b>{bills_count} hóa đơn</b> chưa thanh toán!
            </div>
            <div style="color:#78350f;font-size:0.88rem;margin-top:0.15rem;">
                Tổng cần thanh toán: <b>{money(total_unpaid)}</b> — Vui lòng thanh toán sớm để tránh phát sinh phí.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if first_unpaid:
        st.markdown(
            f"""
            <div style="
                display:inline-block;
                background:#ff5b57;
                color:white;
                border-radius:999px;
                padding:0.4rem 0.85rem;
                font-weight:700;
                font-size:0.8rem;
                margin-bottom:0.9rem;
            ">
                💳 Thanh toán nhanh hóa đơn #{first_unpaid.payment_id} — Kỳ {first_unpaid.period} ({money(first_unpaid.amount)})
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_user_price_suggestion(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header(
        "Gợi ý giá thuê",
        "Xem mức giá khuyến nghị và phân tích AI nội bộ cho phòng bạn quan tâm hoặc đang thuê.",
    )
    with get_db() as db:
        tenant = resolve_tenant_for_user(db, user)
        contracts = []
        if tenant:
            contracts = db.execute(
                select(Contract)
                .where(Contract.tenant_id == tenant.tenant_id, Contract.status == "active")
                .options(joinedload(Contract.room))
                .order_by(Contract.contract_id.desc())
            ).scalars().all()
        rooms = db.execute(select(Room).order_by(Room.room_id.desc())).scalars().all()

    if not rooms:
        st.info("Chưa có dữ liệu phòng")
        return

    preferred_room_id = contracts[0].room_id if contracts else None
    options = {f"{room_code_text(r)} • {r.khu_vuc} • {r.status}": r.room_id for r in rooms}
    labels = list(options.keys())
    default_index = 0
    if preferred_room_id:
        for idx, label in enumerate(labels):
            if options[label] == preferred_room_id:
                default_index = idx
                break

    selected = st.selectbox("Chọn phòng để xem gợi ý", labels, index=default_index)

    with get_db() as db:
        room = db.get(Room, options[selected])
        suggested_price, breakdown = calculate_price_for_room(room)

    c1, c2, c3 = st.columns(3)
    c1.metric("Giá hiện tại", money(room.current_rent))
    c2.metric("Giá gợi ý", money(suggested_price))
    c3.metric(
        "Khung thị trường",
        f"{money(breakdown['khung_gia_thi_truong']['thap_nhat'])} - {money(breakdown['khung_gia_thi_truong']['cao_nhat'])}",
    )

    st.markdown(
        f"""
        <div style="
            background:#eff6ff;
            border:1px solid #bfdbfe;
            border-radius:10px;
            padding:0.85rem 1rem;
            color:#1e3a8a;
            font-size:0.9rem;
            margin:0.7rem 0 1rem;
        ">
            {generate_ai_price_advice(room, suggested_price, breakdown)}
        </div>
        """,
        unsafe_allow_html=True,
    )

    e1, e2 = st.columns([1.15, 0.85])
    with e1:
        st.markdown("#### Thành phần tính giá")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Yếu tố": "Giá trung bình khu vực", "Giá trị": money(breakdown.get("gia_trung_binh_khu_vuc", 0))},
                    {"Yếu tố": "Điều chỉnh diện tích", "Giá trị": money(breakdown.get("dieu_chinh_dien_tich", 0))},
                    {"Yếu tố": "Tổng tiện ích", "Giá trị": money(breakdown.get("tong_tien_tien_ich", 0))},
                    {"Yếu tố": "Thưởng tầng", "Giá trị": money(breakdown.get("thuong_tang", 0))},
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    with e2:
        st.markdown("#### Tiện ích được tính")
        amenities = breakdown.get("tien_ich_ap_dung") or ["Không có"]
        st.write(", ".join(amenities))
        st.caption(f"Nguồn tham chiếu: {breakdown.get('nguon_tham_chieu', 'Nội bộ')}")


def render_sidebar_with_notifications(user: SessionUser) -> str:
    with st.sidebar:
        with get_db() as db:
            unread_count = get_unread_count(db, user.user_id) if user.role != "admin" else 0

        if user.role == "admin":
            menu_items = [
                ("🏠", "Dashboard"),
                ("🚪", "Quản lý phòng"),
                ("👥", "Quản lý người thuê"),
                ("📄", "Quản lý hợp đồng"),
                ("💳", "Quản lý hóa đơn"),
                ("💡", "Gợi ý giá thuê"),
                ("📋", "Audit Log"),
                ("👤", "Quản lý User"),
                ("📨", "📨 Trung tâm Thông báo"),
                ("🤖", "Trợ lý AI"),
            ]
        else:
            notify_label = "Thông báo của tôi"
            if unread_count > 0:
                notify_label = f"Thông báo của tôi  🔴 {unread_count}"
            menu_items = [
                ("🏠", "Danh sách phòng"),
                ("💡", "Gợi ý giá thuê"),
                ("📄", "Hợp đồng của tôi"),
                ("💳", "Hóa đơn của tôi"),
                ("🔔", notify_label),
                ("🤖", "Trợ lý AI"),
            ]

        canonical_labels = [label for _, label in menu_items]
        if "selected_menu" not in st.session_state or st.session_state.selected_menu not in canonical_labels:
            st.session_state.selected_menu = canonical_labels[0]

        st.markdown(
            """
            <style>
            [data-testid="stSidebar"] .stButton > button {
                width: 100% !important;
                text-align: left !important;
                justify-content: flex-start !important;
                padding: 0.52rem 0.8rem !important;
                margin: 0.2rem 0 !important;
                border-radius: 10px !important;
                border: 1px solid #dbe3ee !important;
                background: #ffffff !important;
                color: #111827 !important;
                font-size: 0.9rem !important;
                font-weight: 600 !important;
            }

            [data-testid="stSidebar"] .stButton > button:hover {
                background: #fff1f2 !important;
                border-color: #fda4af !important;
            }

            [data-testid="stSidebar"] .stButton > button[kind="primary"],
            [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {
                background: #ff5b57 !important;
                border-color: #ef4444 !important;
                color: #ffffff !important;
            }

            [data-testid="stSidebar"] .stButton > button[kind="primary"] *,
            [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] * {
                color: #ffffff !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("<div style='height:0.2rem;'></div>", unsafe_allow_html=True)
        for icon, label in menu_items:
            btn_type = "primary" if st.session_state.selected_menu == label else "secondary"
            if st.button(f"{icon}  {label}", key=f"nav_clean_{label}", use_container_width=True, type=btn_type):
                st.session_state.selected_menu = label
                st.rerun()

        st.markdown("<div style='height:0.8rem;'></div>", unsafe_allow_html=True)
        role_label = "Quản trị viên" if user.role == "admin" else "Người thuê"
        st.markdown(
            f"""
            <div class='muted-box' style="margin-top:0.6rem;">
                <div style="font-weight:700;">{user.username}</div>
                <div style="font-size:0.8rem;color:#94a3b8;">{role_label}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("🚪 Đăng xuất", use_container_width=True):
            logout()
            st.rerun()

    selected = st.session_state.selected_menu
    if selected.startswith("Thông báo của tôi"):
        return "Thông báo của tôi"
    return selected


@st.dialog("Thanh toán hóa đơn", width="large")
def render_payment_dialog(payment, user):
    """Modal payment flow that expands the selected method immediately."""
    payment_key = f"payment_method_{payment.payment_id}"
    if payment_key not in st.session_state:
        st.session_state[payment_key] = "momo"

    selected_method = st.session_state[payment_key]

    st.write(f"**Hóa đơn #{payment.payment_id} — Kỳ {payment.period}**")
    st.write(f"🏠 Phòng: {room_code_text(payment.contract.room)}")
    st.write(f"💰 Số tiền: **{money(payment.amount)}**")
    st.divider()

    st.write("**Chọn phương thức thanh toán:**")
    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "📱 Thanh toán MoMo",
            key=f"momo_{payment.payment_id}",
            use_container_width=True,
            type="primary" if selected_method == "momo" else "secondary",
        ):
            st.session_state[payment_key] = "momo"
            selected_method = "momo"

    with col2:
        if st.button(
            "💵 Thanh toán tiền mặt",
            key=f"cash_btn_{payment.payment_id}",
            use_container_width=True,
            type="primary" if selected_method == "cash" else "secondary",
        ):
            st.session_state[payment_key] = "cash"
            selected_method = "cash"

    st.divider()

    if selected_method == "momo":
        st.warning("📱 Quét mã QR để thanh toán qua MoMo")

        note = f"Phong {payment.contract.room.room_code} ky {payment.period} HD{payment.contract_id}"
        amount_int = int(payment.amount)

        try:
            encoded_note = urllib.parse.quote(note)
            momo_url = f"2|99|{MOMO_PHONE}|{MOMO_NAME}||0|0|{amount_int}|{encoded_note}"

            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(momo_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="#ae2070", back_color="white")

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            st.markdown(
                f"""<div style="text-align:center;background:#fff;border-radius:12px;padding:1rem;margin:1rem 0;">
                <img src="data:image/png;base64,{img_base64}" width="220" style="border-radius:8px;" />
                <div style="color:#ae2070;font-weight:600;margin-top:10px;font-size:1.05rem;">SĐT: {MOMO_PHONE}</div>
                <div style="color:#666;font-size:0.9rem;">Chủ tài khoản: {MOMO_NAME}</div>
                <div style="color:#333;font-size:0.85rem;margin-top:8px;">Nội dung: <code>{note}</code></div>
                </div>""",
                unsafe_allow_html=True,
            )
        except Exception as e:
            st.error(f"Lỗi tạo QR: {e}")

        st.info("⏳ Sau khi thanh toán, vui lòng nhấn 'Xác nhận' bên dưới")

        if st.button("✅ Tôi đã thanh toán", key=f"confirm_momo_{payment.payment_id}", type="primary", use_container_width=True):
            with get_db() as db:
                p_updated = mark_payment_paid(db, payment.payment_id, "momo_qr")
                write_audit_log(
                    db, user.user_id, "payments", str(p_updated.payment_id), "payment", new_data=serialize_model(p_updated)
                )
                admin = db.execute(select(User).where(User.role == "admin").limit(1)).scalar_one_or_none()
                if admin:
                    create_notification(
                        db,
                        sender_id=user.user_id,
                        recipient_id=admin.user_id,
                        title=f"Thanh toán MoMo - Phòng {payment.contract.room.room_code}",
                        message=f"Khách {user.full_name} đã thanh toán {money(payment.amount)} (kỳ {payment.period}) qua MoMo.",
                        notification_type="payment",
                    )
                notify_payment_paid(db, p_updated, confirmed_by_admin=False)
            st.session_state.pop(payment_key, None)
            st.success("Thanh toán thành công! Admin sẽ xác nhận sớm.")
            time.sleep(1)
            st.rerun()
    else:
        st.info(
            f"""
💵 **Thanh toán tiền mặt**

Vui lòng liên hệ chủ trọ để thanh toán trực tiếp.

📞 **SĐT:** {MOMO_PHONE}
👤 **Người nhận:** {MOMO_NAME}
💰 **Số tiền:** {money(payment.amount)}
🧾 **Kỳ:** {payment.period}
            """
        )

        if st.button("📞 Tôi sẽ thanh toán trực tiếp", key=f"confirm_cash_{payment.payment_id}", use_container_width=True):
            st.session_state.pop(payment_key, None)
            st.success("Đã ghi nhận. Bạn có thể thanh toán trực tiếp với chủ trọ.")
            time.sleep(1)
            st.rerun()


def render_user_room_catalog(user: SessionUser) -> None:
    hero(APP_NAME)

    with get_db() as db:
        rooms = db.execute(
            select(Room).options(joinedload(Room.images)).order_by(Room.room_id.desc())
        ).unique().scalars().all()

    if not rooms:
        st.info("Chưa có dữ liệu phòng")
        return

    detail_room_id = st.session_state.get("detail_room_id")
    if detail_room_id:
        detail_room = next((r for r in rooms if r.room_id == detail_room_id), None)
        if detail_room:
            if st.button("⬅️ Quay lại danh sách phòng", key="close_detail_top"):
                del st.session_state["detail_room_id"]
                st.session_state.pop(f"room_chat_{detail_room_id}", None)
                st.session_state.pop(f"show_phone_{detail_room_id}", None)
                st.rerun()

            with get_db() as db:
                owner = db.get(User, detail_room.owner_id)

            st.markdown(f"## 🏠 {room_code_text(detail_room)}")
            status_badge = (
                '<span style="background:#22c55e;color:white;padding:2px 10px;border-radius:12px;font-size:0.85rem;">Còn trống</span>'
                if detail_room.status == "available"
                else '<span style="background:#ef4444;color:white;padding:2px 10px;border-radius:12px;font-size:0.85rem;">Đang thuê</span>'
            )
            st.markdown(status_badge, unsafe_allow_html=True)
            st.markdown("")

            col_img, col_info = st.columns([1.3, 1])
            with col_img:
                if detail_room.images:
                    valid_imgs = [img for img in detail_room.images if os.path.exists(img.image_url)]
                    if valid_imgs:
                        for img in valid_imgs[:3]:
                            st.image(img.image_url, use_container_width=True)
                    else:
                        st.image("https://placehold.co/600x400/e2e8f0/94a3b8?text=Phong+Tro", use_container_width=True)
                else:
                    st.image("https://placehold.co/600x400/e2e8f0/94a3b8?text=Phong+Tro", use_container_width=True)

            with col_info:
                st.markdown("### 📋 Thông tin chi tiết")
                st.markdown(f"- **Mã phòng:** {detail_room.room_code}")
                st.markdown(f"- **Khu vực:** {detail_room.khu_vuc}")
                st.markdown(f"- **Địa chỉ:** {detail_room.address or 'Liên hệ admin'}")
                st.markdown(f"- **Diện tích:** {float(detail_room.area_m2)} m²")
                st.markdown(f"- **Tầng:** {detail_room.tang}")

                st.markdown("---")
                st.markdown("### 💰 Giá thuê")
                suggested, _ = calculate_price_for_room(detail_room)
                st.markdown(
                    f"<div style='font-size:1.4rem;font-weight:700;color:#3b82f6;'>{money(detail_room.current_rent)}"
                    f"<span style='font-size:0.9rem;color:#94a3b8;'>/tháng</span></div>",
                    unsafe_allow_html=True,
                )
                st.caption(f"AI gợi ý: {money(suggested)}/tháng")

                st.markdown("---")
                st.markdown("### ✨ Tiện ích")
                amenity_list = []
                if detail_room.has_aircon:
                    amenity_list.append("❄️ Máy lạnh")
                if detail_room.has_fridge:
                    amenity_list.append("🧊 Tủ lạnh")
                if detail_room.has_water_heater:
                    amenity_list.append("🚿 Bình nóng lạnh")
                if detail_room.has_balcony:
                    amenity_list.append("🌿 Ban công")
                if detail_room.has_elevator:
                    amenity_list.append("🛗 Thang máy")
                if amenity_list:
                    cols_am = st.columns(2)
                    for idx, amenity in enumerate(amenity_list):
                        cols_am[idx % 2].markdown(f"  {amenity}")
                else:
                    st.markdown("  Cơ bản")

            st.markdown("---")
            st.markdown("### 📞 Thông tin liên hệ")
            contact_cols = st.columns([1.5, 1, 1])
            owner_name = owner.full_name if owner else "Chủ trọ"
            owner_phone = owner.phone if owner and owner.phone else MOMO_PHONE
            show_phone_key = f"show_phone_{detail_room_id}"

            with contact_cols[0]:
                phone_text = owner_phone or "Liên hệ qua hệ thống"
                st.markdown(
                    f"""<div style="border:1px solid #334155;border-radius:12px;padding:1rem;">
                    <div style="font-weight:600;font-size:1rem;">👤 {owner_name}</div>
                    <div style="color:#94a3b8;font-size:0.85rem;">Chủ nhà / Quản lý</div>
                    <div style="margin-top:0.4rem;font-size:0.95rem;">📱 <b>{phone_text}</b></div>
                    </div>""",
                    unsafe_allow_html=True,
                )

            with contact_cols[1]:
                if owner_phone:
                    if st.button("📞 Gọi điện", key=f"show_phone_btn_{detail_room_id}", use_container_width=True, type="primary"):
                        st.session_state[show_phone_key] = not st.session_state.get(show_phone_key, False)
                else:
                    st.button("📞 Gọi điện", disabled=True, use_container_width=True)

            with contact_cols[2]:
                chat_key = f"show_chat_{detail_room_id}"
                if st.button("💬 Nhắn tin với Admin", key=f"open_chat_{detail_room_id}", use_container_width=True):
                    st.session_state[chat_key] = not st.session_state.get(chat_key, False)
                    st.rerun()

            if owner_phone and st.session_state.get(show_phone_key, False):
                st.success(f"📞 Số điện thoại Admin: {owner_phone}")

            chat_session_key = f"room_chat_{detail_room_id}"
            if st.session_state.get(chat_key, False):
                st.markdown("#### 💬 Nhắn tin về phòng này")
                st.caption("Trả lời tự động các câu hỏi cơ bản. Câu hỏi về hợp đồng/thanh toán sẽ chuyển đến admin.")

                if chat_session_key not in st.session_state:
                    st.session_state[chat_session_key] = [
                        {
                            "role": "bot",
                            "text": (
                                f"Xin chào! Tôi là trợ lý tự động của phòng **{detail_room.room_code}**.\n\n"
                                f"Bạn có thể hỏi tôi về:\n"
                                f"- Giá thuê, diện tích, tầng\n"
                                f"- Tiện ích\n"
                                f"- Tình trạng còn trống\n"
                                f"- Địa chỉ / khu vực\n\n"
                                f"Câu hỏi về hợp đồng hoặc thanh toán sẽ được chuyển đến admin."
                            ),
                        }
                    ]

                chat_msgs = st.session_state[chat_session_key]
                with st.container():
                    for msg in chat_msgs:
                        if msg["role"] == "user":
                            with st.chat_message("user"):
                                st.markdown(msg["text"])
                        else:
                            with st.chat_message("assistant"):
                                st.markdown(msg["text"])

                if user_msg := st.chat_input(f"Hỏi về phòng {detail_room.room_code}...", key=f"chat_input_{detail_room_id}"):
                    chat_msgs.append({"role": "user", "text": user_msg})
                    reply, forward_to_admin = room_contact_auto_reply(user_msg, detail_room)
                    chat_msgs.append({"role": "bot", "text": reply})

                    if forward_to_admin:
                        with get_db() as db:
                            admin = db.execute(select(User).where(User.role == "admin").limit(1)).scalar_one_or_none()
                            if admin:
                                from services.notification_service import send_user_to_admin_message

                                send_user_to_admin_message(
                                    db,
                                    user_id=user.user_id,
                                    admin_user_id=admin.user_id,
                                    title=f"Khách hỏi về phòng {detail_room.room_code}",
                                    message=f"{user.full_name}: {user_msg}",
                                    notification_type="general",
                                    related_entity_type="room",
                                    related_entity_id=detail_room.room_id,
                                )

                    st.session_state[chat_session_key] = chat_msgs
                    st.rerun()

            return

    section_header("Danh sách phòng", "Tra cứu nhanh phòng và mức giá gợi ý để so sánh trước khi thuê.")

    c1, c2, c3 = st.columns([2, 1, 1])
    keyword = c1.text_input("Tìm kiếm phòng", placeholder="Nhập mã phòng, khu vực, địa chỉ...")
    status_filter = c2.selectbox("Trạng thái phòng", ["Tất cả"] + sorted({display_status(r.status) for r in rooms}))
    region_filter = c3.selectbox("Khu vực", ["Tất cả"] + sorted({r.khu_vuc for r in rooms}))

    filtered_rooms = []
    for room in rooms:
        if keyword and keyword.lower() not in normalize_room_search(room):
            continue
        if status_filter != "Tất cả" and room.status != db_status(status_filter):
            continue
        if region_filter != "Tất cả" and room.khu_vuc != region_filter:
            continue
        filtered_rooms.append(room)

    if not filtered_rooms:
        st.info("Không có phòng phù hợp với bộ lọc")
        return

    cols = st.columns(3)
    for i, room in enumerate(filtered_rooms):
        suggested_price, _ = calculate_price_for_room(room)
        with cols[i % 3]:
            with st.container(border=True):
                if room.images:
                    img_path = room.images[0].image_url
                    if os.path.exists(img_path):
                        st.image(img_path, width=400)
                    else:
                        st.image("https://placehold.co/400x250/e2e8f0/94a3b8?text=Phong+Tro", width=400)
                else:
                    st.image("https://placehold.co/400x250/e2e8f0/94a3b8?text=Phong+Tro", width=400)
                st.markdown(f"### {room_code_text(room)}")
                st.markdown(f"📍 **Khu vực:** {room.khu_vuc}")
                st.markdown(f"📏 **Diện tích:** {float(room.area_m2)} m²")
                st.markdown(f"🏢 **Tầng:** {room.tang}")
                st.markdown(f"💰 **Giá thuê:** {money(room.current_rent)}")

                amenities = []
                if room.has_aircon:
                    amenities.append("Máy lạnh")
                if room.has_fridge:
                    amenities.append("Tủ lạnh")
                if room.has_water_heater:
                    amenities.append("Nóng lạnh")
                if amenities:
                    st.markdown(f"✨ **Tiện ích:** {', '.join(amenities)}")

                status_color = "green" if room.status == "available" else "red"
                status_text = "Còn trống" if room.status == "available" else "Đang thuê"
                st.markdown(f"**Trạng thái:** :{status_color}[{status_text}]")
                st.caption(f"AI gợi ý: {money(suggested_price)}")

                if st.button(f"Xem chi tiết #{room.room_id}", key=f"view_room_{room.room_id}", use_container_width=True):
                    st.session_state["detail_room_id"] = room.room_id
                    st.rerun()

def get_room_chat_messages(db: Session, *, admin_user_id: int, tenant_user_id: int, room_id: int) -> list[Notification]:
    return db.execute(
        select(Notification)
        .options(joinedload(Notification.sender))
        .where(
            Notification.is_system == False,
            Notification.related_entity_type == "room",
            Notification.related_entity_id == room_id,
            or_(
                (Notification.sender_id == tenant_user_id) & (Notification.recipient_id == admin_user_id),
                (Notification.sender_id == admin_user_id) & (Notification.recipient_id == tenant_user_id),
            ),
        )
        .order_by(Notification.created_at.asc(), Notification.notification_id.asc())
    ).scalars().all()


def _render_chat_bubble(message: str, sender_label: str, created_at: datetime, *, is_self: bool) -> None:
    align = "flex-end" if is_self else "flex-start"
    bg = "#0084ff" if is_self else "#f3f4f6"
    color = "#ffffff" if is_self else "#111827"
    meta_color = "#dbeafe" if is_self else "#6b7280"
    safe_message = html.escape(message).replace("\n", "<br>")
    safe_sender = html.escape(sender_label)
    safe_meta = html.escape(created_at.strftime("%d/%m/%Y %H:%M"))
    st.markdown(
        f"""
        <div style="display:flex;justify-content:{align};margin:0.45rem 0;">
            <div style="max-width:78%;">
                <div style="
                    background:{bg};
                    color:{color};
                    border-radius:18px;
                    padding:0.72rem 0.95rem;
                    box-shadow:0 1px 2px rgba(15,23,42,0.08);
                    line-height:1.45;
                    word-break:break-word;
                ">{safe_message}</div>
                <div style="font-size:0.75rem;color:{meta_color};margin-top:0.2rem;padding:0 0.35rem;">
                    {safe_sender} • {safe_meta}
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_chat_thread_item(label: str, subtitle: str, preview: str, *, is_active: bool, button_key: str) -> bool:
    border = "#fb7185" if is_active else "#e5e7eb"
    bg = "#fff1f2" if is_active else "#ffffff"
    safe_label = html.escape(label)
    safe_subtitle = html.escape(subtitle)
    safe_preview = html.escape(preview[:80] + ("..." if len(preview) > 80 else ""))
    st.markdown(
        f"""
        <div style="
            border:1px solid {border};
            background:{bg};
            border-radius:14px;
            padding:0.85rem 0.9rem;
            margin-bottom:0.55rem;
        ">
            <div style="font-weight:700;color:#111827;">{safe_label}</div>
            <div style="font-size:0.8rem;color:#64748b;margin-top:0.15rem;">{safe_subtitle}</div>
            <div style="font-size:0.84rem;color:#374151;margin-top:0.45rem;">{safe_preview}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return st.button("Mở chat", key=button_key, use_container_width=True, type="primary" if is_active else "secondary")


def room_contact_auto_reply(message: str, room) -> tuple[str, bool]:
    msg = (message or "").lower().strip()

    if any(k in msg for k in ["xin chào", "chào", "hello", "hi", "alo"]):
        return (
            f"Xin chào! Tôi là trợ lý tự động của phòng **{room.room_code}**. "
            f"Tôi sẽ hỗ trợ các câu hỏi cơ bản trước, còn khi bạn cần tư vấn thuê phòng cụ thể thì admin sẽ vào chat trực tiếp.",
            False,
        )

    if any(k in msg for k in ["giá", "tiền thuê", "bao nhiêu", "chi phí", "giá thuê"]):
        amenities = []
        if room.has_aircon: amenities.append("máy lạnh")
        if room.has_fridge: amenities.append("tủ lạnh")
        if room.has_water_heater: amenities.append("bình nóng lạnh")
        if room.has_balcony: amenities.append("ban công")
        if room.has_elevator: amenities.append("thang máy")
        amenity_str = ", ".join(amenities) if amenities else "cơ bản"
        return (
            f"Phòng **{room.room_code}** có giá thuê **{money(room.current_rent)}/tháng**.\n"
            f"- Diện tích: {float(room.area_m2)} m²\n"
            f"- Tầng: {room.tang} | Khu vực: {room.khu_vuc}\n"
            f"- Tiện ích: {amenity_str}",
            False,
        )

    if any(k in msg for k in ["còn trống", "có phòng", "tình trạng", "trạng thái", "available", "đang thuê"]):
        if room.status == "available":
            return (f"Phòng **{room.room_code}** hiện còn trống và sẵn sàng cho thuê.", False)
        return (f"Phòng **{room.room_code}** hiện đang có người thuê.", False)

    if any(k in msg for k in ["tiện nghi", "tiện ích", "nội thất", "máy lạnh", "tủ lạnh", "nóng lạnh", "ban công", "thang máy", "wifi"]):
        amenities = []
        if room.has_aircon: amenities.append("❄️ Máy lạnh")
        if room.has_fridge: amenities.append("🧊 Tủ lạnh")
        if room.has_water_heater: amenities.append("🚿 Bình nóng lạnh")
        if room.has_balcony: amenities.append("🌿 Ban công")
        if room.has_elevator: amenities.append("🛗 Thang máy")
        return (f"Phòng **{room.room_code}** có các tiện ích: {', '.join(amenities) if amenities else 'cơ bản'}.", False)

    if any(k in msg for k in ["địa chỉ", "vị trí", "đường", "quận", "khu vực", "ở đâu", "chỗ nào"]):
        return (
            f"Phòng **{room.room_code}** nằm tại khu vực **{room.khu_vuc}**, tầng {room.tang}. "
            f"Địa chỉ: {room.address or 'liên hệ admin để biết thêm'}.",
            False,
        )

    if any(k in msg for k in ["diện tích", "rộng", "m2", "m²", "mét vuông"]):
        return (f"Phòng **{room.room_code}** có diện tích **{float(room.area_m2)} m²**.", False)

    if any(k in msg for k in ["thuê", "xem phòng", "giữ phòng", "đặt phòng", "đặt cọc", "cọc", "hợp đồng", "thủ tục", "cmnd", "căn cước", "ký hợp đồng", "bao giờ xem phòng"]):
        return (
            "Yêu cầu thuê phòng của bạn đã được ghi nhận. **Admin sẽ vào chat trực tiếp với bạn** để tư vấn chi tiết về xem phòng, giữ phòng, đặt cọc và hợp đồng.",
            True,
        )

    if any(k in msg for k in ["thanh toán", "chuyển khoản", "ngân hàng", "momo", "tiền cọc"]):
        return (
            "Vấn đề thanh toán cần admin xác nhận. **Admin sẽ vào chat trực tiếp với bạn** để hướng dẫn chi tiết.",
            True,
        )

    return (
        "Tôi đã ghi nhận câu hỏi của bạn. **Admin sẽ vào chat trực tiếp với bạn** để hỗ trợ rõ hơn.",
        True,
    )


def render_admin_notifications(user: SessionUser) -> None:
    hero(APP_NAME)
    section_header("📨 Trung tâm Thông báo", "Gửi thông báo cho người thuê và chat trực tiếp khi user hỏi về thuê phòng.")
    tabs = st.tabs(["📤 Gửi thông báo", "💬 Chat với User", "⚙️ Thông báo hệ thống", "🤖 Tự động hóa AI"])

    with get_db() as db:
        all_users = db.execute(select(User).where(User.role == "user").order_by(User.full_name)).scalars().all()
        tenants = db.execute(
            select(Tenant)
            .options(joinedload(Tenant.user), joinedload(Tenant.contracts).joinedload(Contract.room))
            .order_by(Tenant.full_name)
        ).unique().scalars().all()

    with tabs[0]:
        st.markdown("### 📤 Gửi thông báo đến người thuê")
        user_options = {"Tất cả người thuê": "all"}
        for u in all_users:
            tenant_info = next((t for t in tenants if t.user_id == u.user_id), None)
            label = f"{u.full_name} ({u.username})"
            if tenant_info and tenant_info.contracts:
                active_contract = next((c for c in tenant_info.contracts if c.status == "active"), None)
                if active_contract and active_contract.room:
                    label += f" - Phòng: {active_contract.room.room_code}"
            user_options[label] = u.user_id

        selected_user = st.selectbox("Chọn người nhận", list(user_options.keys()))
        col1, col2 = st.columns(2)
        with col1:
            title = st.text_input("Tiêu đề thông báo", placeholder="VD: Thông báo thu tiền trễ...")
        with col2:
            notification_type = st.selectbox("Loại thông báo", ["general", "payment", "contract", "maintenance"])
        message = st.text_area("Nội dung thông báo", placeholder="Nhập nội dung chi tiết...", height=120)

        if st.button("📨 Gửi thông báo", type="primary", disabled=not (title and message)):
            with get_db() as db:
                target_id = user_options[selected_user]
                if target_id == "all":
                    for u in all_users:
                        send_admin_to_user_message(db, user.user_id, u.user_id, title, message, notification_type)
                    st.success(f"Đã gửi thông báo đến {len(all_users)} người dùng.")
                else:
                    send_admin_to_user_message(db, user.user_id, target_id, title, message, notification_type)
                    st.success(f"Đã gửi thông báo đến {selected_user}.")

    with tabs[1]:
        st.markdown("### 💬 Chat với User")
        with get_db() as db:
            room_threads = db.execute(
                select(Notification)
                .options(joinedload(Notification.sender))
                .where(
                    Notification.recipient_id == user.user_id,
                    Notification.is_system == False,
                    Notification.related_entity_type == "room",
                )
                .order_by(Notification.created_at.desc())
            ).scalars().all()

            thread_map: dict[tuple[int, int], Notification] = {}
            for msg in room_threads:
                if not msg.sender_id or not msg.related_entity_id:
                    continue
                key = (msg.sender_id, msg.related_entity_id)
                if key not in thread_map:
                    thread_map[key] = msg

            if not thread_map:
                st.info("Chưa có cuộc chat thuê phòng nào từ user.")
            else:
                thread_items = []
                for (sender_id, room_id), last_msg in thread_map.items():
                    room = db.get(Room, room_id)
                    sender_name = last_msg.sender.full_name if last_msg.sender else f"User {sender_id}"
                    thread_items.append(
                        {
                            "thread_id": f"{sender_id}:{room_id}",
                            "sender_id": sender_id,
                            "room_id": room_id,
                            "sender_name": sender_name,
                            "room_code": room_code_text(room),
                            "preview": last_msg.message,
                            "created_at": last_msg.created_at,
                            "unread": not last_msg.is_read,
                        }
                    )

                thread_items.sort(key=lambda item: item["created_at"], reverse=True)
                selected_thread_id = st.session_state.get("admin_selected_room_thread")
                available_thread_ids = {item["thread_id"] for item in thread_items}
                if selected_thread_id not in available_thread_ids:
                    selected_thread_id = thread_items[0]["thread_id"]
                    st.session_state["admin_selected_room_thread"] = selected_thread_id

                left_col, right_col = st.columns([0.92, 1.58], gap="large")

                with left_col:
                    st.markdown("#### Hội thoại")
                    for item in thread_items:
                        title = f"{item['sender_name']}{' 🔴' if item['unread'] else ''}"
                        subtitle = f"Phòng {item['room_code']}"
                        preview = item["preview"] or "(không có nội dung)"
                        opened = _render_chat_thread_item(
                            title,
                            subtitle,
                            preview,
                            is_active=item["thread_id"] == selected_thread_id,
                            button_key=f"open_admin_thread_{item['thread_id']}",
                        )
                        if opened:
                            st.session_state["admin_selected_room_thread"] = item["thread_id"]
                            st.rerun()

                with right_col:
                    current_thread = next(item for item in thread_items if item["thread_id"] == st.session_state["admin_selected_room_thread"])
                    tenant_user_id = current_thread["sender_id"]
                    room_id = current_thread["room_id"]
                    tenant_user = db.get(User, tenant_user_id)
                    room = db.get(Room, room_id)
                    messages = get_room_chat_messages(db, admin_user_id=user.user_id, tenant_user_id=tenant_user_id, room_id=room_id)

                    for msg in messages:
                        if msg.recipient_id == user.user_id and not msg.is_read:
                            msg.is_read = True

                    st.markdown(
                        f"""
                        <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;padding:1rem 1.1rem;margin-bottom:0.8rem;">
                            <div style="font-size:1.05rem;font-weight:800;color:#111827;">{html.escape(tenant_user.full_name if tenant_user else str(tenant_user_id))}</div>
                            <div style="color:#64748b;font-size:0.88rem;margin-top:0.1rem;">Phòng {html.escape(room_code_text(room))}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    st.markdown("<div style='background:#f8fafc;border:1px solid #e5e7eb;border-radius:18px;padding:1rem 1rem 0.8rem;'>", unsafe_allow_html=True)
                    for msg in messages:
                        _render_chat_bubble(
                            msg.message,
                            "Admin" if msg.sender_id == user.user_id else (msg.sender.full_name if msg.sender else "User"),
                            msg.created_at,
                            is_self=msg.sender_id == user.user_id,
                        )
                    st.markdown("</div>", unsafe_allow_html=True)

                    with st.form(key=f"reply_form_{tenant_user_id}_{room_id}", clear_on_submit=True):
                        reply_text = st.text_area(
                            "Nhắn tin",
                            key=f"admin_room_reply_{tenant_user_id}_{room_id}",
                            placeholder="Nhập tin nhắn cho user...",
                            label_visibility="collapsed",
                            height=90,
                        )
                        submitted = st.form_submit_button("Gửi", type="primary", use_container_width=True)

                    if submitted and reply_text.strip():
                        create_notification(
                            db,
                            sender_id=user.user_id,
                            recipient_id=tenant_user_id,
                            title=f"Phản hồi về phòng {room_code_text(room)}",
                            message=reply_text.strip(),
                            notification_type="message",
                            related_entity_type="room",
                            related_entity_id=room_id,
                            is_system=False,
                        )
                        st.success("Đã gửi phản hồi cho user.")
                        st.rerun()

    with tabs[2]:
        st.markdown("### ⚙️ Thông báo hệ thống")
        with get_db() as db:
            system_notifs = db.execute(
                select(Notification)
                .where(Notification.recipient_id == user.user_id, Notification.is_system == True)
                .order_by(Notification.created_at.desc())
                .limit(50)
            ).scalars().all()
        if system_notifs:
            for notif in system_notifs:
                with st.container(border=True):
                    st.markdown(f"**{notif.title}**")
                    st.caption(notif.created_at.strftime('%d/%m/%Y %H:%M'))
                    st.markdown(notif.message)
        else:
            st.info("Chưa có thông báo hệ thống")

    with tabs[3]:
        render_ai_automation_dashboard(user)


def render_user_room_catalog(user: SessionUser) -> None:
    hero(APP_NAME)

    with get_db() as db:
        rooms = db.execute(select(Room).options(joinedload(Room.images)).order_by(Room.room_id.desc())).unique().scalars().all()

    if not rooms:
        st.info("Chưa có dữ liệu phòng")
        return

    detail_room_id = st.session_state.get("detail_room_id")
    if detail_room_id:
        detail_room = next((r for r in rooms if r.room_id == detail_room_id), None)
        if detail_room:
            if st.button("⬅️ Quay lại danh sách phòng", key="close_detail_top"):
                del st.session_state["detail_room_id"]
                st.session_state.pop(f"show_phone_{detail_room_id}", None)
                st.rerun()

            with get_db() as db:
                owner = db.get(User, detail_room.owner_id)

            st.markdown(f"## 🏠 {room_code_text(detail_room)}")
            status_badge = '<span style="background:#22c55e;color:white;padding:2px 10px;border-radius:12px;font-size:0.85rem;">Còn trống</span>' if detail_room.status == "available" else '<span style="background:#ef4444;color:white;padding:2px 10px;border-radius:12px;font-size:0.85rem;">Đang thuê</span>'
            st.markdown(status_badge, unsafe_allow_html=True)
            st.markdown("")

            col_img, col_info = st.columns([1.3, 1])
            with col_img:
                if detail_room.images:
                    valid_imgs = [img for img in detail_room.images if os.path.exists(img.image_url)]
                    if valid_imgs:
                        for img in valid_imgs[:3]:
                            st.image(img.image_url, use_container_width=True)
                    else:
                        st.image("https://placehold.co/600x400/e2e8f0/94a3b8?text=Phong+Tro", use_container_width=True)
                else:
                    st.image("https://placehold.co/600x400/e2e8f0/94a3b8?text=Phong+Tro", use_container_width=True)

            with col_info:
                st.markdown("### 📋 Thông tin chi tiết")
                st.markdown(f"- **Mã phòng:** {detail_room.room_code}")
                st.markdown(f"- **Khu vực:** {detail_room.khu_vuc}")
                st.markdown(f"- **Địa chỉ:** {detail_room.address or 'Liên hệ admin'}")
                st.markdown(f"- **Diện tích:** {float(detail_room.area_m2)} m²")
                st.markdown(f"- **Tầng:** {detail_room.tang}")
                st.markdown("---")
                st.markdown("### 💰 Giá thuê")
                suggested, _ = calculate_price_for_room(detail_room)
                st.markdown(f"<div style='font-size:1.4rem;font-weight:700;color:#3b82f6;'>{money(detail_room.current_rent)}<span style='font-size:0.9rem;color:#94a3b8;'>/tháng</span></div>", unsafe_allow_html=True)
                st.caption(f"AI gợi ý: {money(suggested)}/tháng")
                st.markdown("---")
                st.markdown("### ✨ Tiện ích")
                amenity_list = []
                if detail_room.has_aircon: amenity_list.append("❄️ Máy lạnh")
                if detail_room.has_fridge: amenity_list.append("🧊 Tủ lạnh")
                if detail_room.has_water_heater: amenity_list.append("🚿 Bình nóng lạnh")
                if detail_room.has_balcony: amenity_list.append("🌿 Ban công")
                if detail_room.has_elevator: amenity_list.append("🛗 Thang máy")
                if amenity_list:
                    cols_am = st.columns(2)
                    for idx, amenity in enumerate(amenity_list):
                        cols_am[idx % 2].markdown(f"  {amenity}")
                else:
                    st.markdown("  Cơ bản")

            st.markdown("---")
            st.markdown("### 📞 Thông tin liên hệ")
            contact_cols = st.columns([1.5, 1, 1])
            owner_name = owner.full_name if owner else "Chủ trọ"
            owner_phone = owner.phone if owner and owner.phone else MOMO_PHONE
            show_phone_key = f"show_phone_{detail_room_id}"
            with contact_cols[0]:
                phone_text = owner_phone or "Liên hệ qua hệ thống"
                st.markdown(
                    f"""<div style="border:1px solid #334155;border-radius:12px;padding:1rem;">
                    <div style="font-weight:600;font-size:1rem;">👤 {owner_name}</div>
                    <div style="color:#94a3b8;font-size:0.85rem;">Chủ nhà / Quản lý</div>
                    <div style="margin-top:0.4rem;font-size:0.95rem;">📱 <b>{phone_text}</b></div>
                    </div>""",
                    unsafe_allow_html=True,
                )
            with contact_cols[1]:
                if owner_phone:
                    if st.button("📞 Gọi điện", key=f"show_phone_btn_{detail_room_id}", use_container_width=True, type="primary"):
                        st.session_state[show_phone_key] = not st.session_state.get(show_phone_key, False)
                else:
                    st.button("📞 Gọi điện", disabled=True, use_container_width=True)
            with contact_cols[2]:
                chat_key = f"show_chat_{detail_room_id}"
                if st.button("💬 Nhắn tin với Admin", key=f"open_chat_{detail_room_id}", use_container_width=True):
                    st.session_state[chat_key] = not st.session_state.get(chat_key, False)
                    st.rerun()

            if owner_phone and st.session_state.get(show_phone_key, False):
                st.success(f"📞 Số điện thoại Admin: {owner_phone}")

            chat_key = f"show_chat_{detail_room_id}"
            if st.session_state.get(chat_key, False):
                st.markdown("#### 💬 Nhắn tin về phòng này")
                st.caption("Kiểu hội thoại trực tiếp với admin. Trợ lý tự động chỉ xử lý các câu hỏi cơ bản ban đầu.")

                with get_db() as db:
                    admin = db.execute(select(User).where(User.role == "admin").limit(1)).scalar_one_or_none()
                    db_messages = get_room_chat_messages(
                        db,
                        admin_user_id=admin.user_id if admin else detail_room.owner_id,
                        tenant_user_id=user.user_id,
                        room_id=detail_room.room_id,
                    ) if admin else []
                    for msg in db_messages:
                        if msg.recipient_id == user.user_id and not msg.is_read:
                            msg.is_read = True

                st.markdown(
                    f"""
                    <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;padding:1rem 1.1rem;margin-bottom:0.8rem;">
                        <div style="font-size:1.05rem;font-weight:800;color:#111827;">Admin</div>
                        <div style="color:#64748b;font-size:0.88rem;margin-top:0.1rem;">Tư vấn thuê phòng {html.escape(room_code_text(detail_room))}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                st.markdown("<div style='background:#f8fafc;border:1px solid #e5e7eb;border-radius:18px;padding:1rem 1rem 0.8rem;'>", unsafe_allow_html=True)
                _render_chat_bubble(
                    (
                        f"Xin chào! Tôi là trợ lý tự động của phòng {detail_room.room_code}. "
                        f"Tôi hỗ trợ các câu hỏi cơ bản. Nếu bạn muốn thuê thật, đặt cọc, xem phòng hoặc làm hợp đồng, admin sẽ vào chat trực tiếp."
                    ),
                    "Trợ lý",
                    datetime.now(),
                    is_self=False,
                )
                for msg in db_messages:
                    _render_chat_bubble(
                        msg.message,
                        "Bạn" if msg.sender_id == user.user_id else "Admin",
                        msg.created_at,
                        is_self=msg.sender_id == user.user_id,
                    )
                st.markdown("</div>", unsafe_allow_html=True)

                user_msg = st.chat_input(f"Nhập tin nhắn về phòng {detail_room.room_code}...", key=f"user_room_chat_input_{detail_room_id}")
                if user_msg and user_msg.strip():
                    reply, forward_to_admin = room_contact_auto_reply(user_msg, detail_room)
                    if forward_to_admin and admin:
                        with get_db() as db:
                            create_notification(
                                db,
                                sender_id=user.user_id,
                                recipient_id=admin.user_id,
                                title=f"Khách hỏi về phòng {detail_room.room_code}",
                                message=user_msg.strip(),
                                notification_type="message",
                                related_entity_type="room",
                                related_entity_id=detail_room.room_id,
                                is_system=False,
                            )
                            create_notification(
                                db,
                                sender_id=admin.user_id,
                                recipient_id=user.user_id,
                                title=f"Tự động phản hồi về phòng {detail_room.room_code}",
                                message=reply,
                                notification_type="message",
                                related_entity_type="room",
                                related_entity_id=detail_room.room_id,
                                is_system=False,
                            )
                        st.info("Đã chuyển cuộc trò chuyện cho admin. Admin sẽ phản hồi trực tiếp trong khung chat này.")
                    else:
                        with get_db() as db:
                            create_notification(
                                db,
                                sender_id=user.user_id,
                                recipient_id=admin.user_id if admin else detail_room.owner_id,
                                title=f"User hỏi nhanh về phòng {detail_room.room_code}",
                                message=user_msg.strip(),
                                notification_type="message",
                                related_entity_type="room",
                                related_entity_id=detail_room.room_id,
                                is_system=False,
                            )
                            if admin:
                                create_notification(
                                    db,
                                    sender_id=admin.user_id,
                                    recipient_id=user.user_id,
                                    title=f"Tự động phản hồi về phòng {detail_room.room_code}",
                                    message=reply,
                                    notification_type="message",
                                    related_entity_type="room",
                                    related_entity_id=detail_room.room_id,
                                    is_system=False,
                                )
                    st.rerun()

            return

    section_header("Danh sách phòng", "Tra cứu nhanh phòng và mức giá gợi ý để so sánh trước khi thuê.")
    c1, c2, c3 = st.columns([2, 1, 1])
    keyword = c1.text_input("Tìm kiếm phòng", placeholder="Nhập mã phòng, khu vực, địa chỉ...")
    status_filter = c2.selectbox("Trạng thái phòng", ["Tất cả"] + sorted({display_status(r.status) for r in rooms}))
    region_filter = c3.selectbox("Khu vực", ["Tất cả"] + sorted({r.khu_vuc for r in rooms}))

    filtered_rooms = []
    for room in rooms:
        if keyword and keyword.lower() not in normalize_room_search(room):
            continue
        if status_filter != "Tất cả" and room.status != db_status(status_filter):
            continue
        if region_filter != "Tất cả" and room.khu_vuc != region_filter:
            continue
        filtered_rooms.append(room)

    if not filtered_rooms:
        st.info("Không có phòng phù hợp với bộ lọc")
        return

    cols = st.columns(3)
    for i, room in enumerate(filtered_rooms):
        suggested_price, _ = calculate_price_for_room(room)
        with cols[i % 3]:
            with st.container(border=True):
                if room.images:
                    img_path = room.images[0].image_url
                    if os.path.exists(img_path):
                        st.image(img_path, width=400)
                    else:
                        st.image("https://placehold.co/400x250/e2e8f0/94a3b8?text=Phong+Tro", width=400)
                else:
                    st.image("https://placehold.co/400x250/e2e8f0/94a3b8?text=Phong+Tro", width=400)
                st.markdown(f"### {room_code_text(room)}")
                st.markdown(f"📍 **Khu vực:** {room.khu_vuc}")
                st.markdown(f"📏 **Diện tích:** {float(room.area_m2)} m²")
                st.markdown(f"🏢 **Tầng:** {room.tang}")
                st.markdown(f"💰 **Giá thuê:** {money(room.current_rent)}")
                amenities = []
                if room.has_aircon: amenities.append("Máy lạnh")
                if room.has_fridge: amenities.append("Tủ lạnh")
                if room.has_water_heater: amenities.append("Nóng lạnh")
                if amenities:
                    st.markdown(f"✨ **Tiện ích:** {', '.join(amenities)}")
                status_color = "green" if room.status == "available" else "red"
                status_text = "Còn trống" if room.status == "available" else "Đang thuê"
                st.markdown(f"**Trạng thái:** :{status_color}[{status_text}]")
                st.caption(f"AI gợi ý: {money(suggested_price)}")
                if st.button(f"Xem chi tiết #{room.room_id}", key=f"view_room_{room.room_id}", use_container_width=True):
                    st.session_state["detail_room_id"] = room.room_id
                    st.rerun()


if __name__ == "__main__":
    main()
