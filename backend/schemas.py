"""
Pydantic schemas – Request & Response models
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, EmailStr, field_validator


# ══════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    full_name: str
    role: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None


# ══════════════════════════════════════════════════════════════════
# USER
# ══════════════════════════════════════════════════════════════════
class UserOut(BaseModel):
    user_id: int
    username: str
    full_name: str
    email: Optional[str]
    phone: Optional[str]
    role: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class UserStatusUpdate(BaseModel):
    status: str  # "active" | "locked"

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in ("active", "locked"):
            raise ValueError("status phải là 'active' hoặc 'locked'")
        return v


# ══════════════════════════════════════════════════════════════════
# ROOM
# ══════════════════════════════════════════════════════════════════
class RoomImageOut(BaseModel):
    image_id: int
    image_url: str
    is_primary: bool

    class Config:
        from_attributes = True


class RoomOut(BaseModel):
    room_id: int
    owner_id: int
    room_code: str
    area_m2: float
    khu_vuc: str
    tang: int
    address: Optional[str]
    current_rent: float
    status: str
    has_aircon: bool
    has_fridge: bool
    has_water_heater: bool
    has_balcony: bool
    has_elevator: bool
    images: List[RoomImageOut] = []
    created_at: datetime

    class Config:
        from_attributes = True


class RoomCreate(BaseModel):
    room_code: str
    area_m2: float
    khu_vuc: str
    tang: int = 1
    address: Optional[str] = None
    current_rent: float = 0
    has_aircon: bool = False
    has_fridge: bool = False
    has_water_heater: bool = False
    has_balcony: bool = False
    has_elevator: bool = False


class RoomUpdate(BaseModel):
    area_m2: Optional[float] = None
    khu_vuc: Optional[str] = None
    tang: Optional[int] = None
    address: Optional[str] = None
    current_rent: Optional[float] = None
    status: Optional[str] = None
    has_aircon: Optional[bool] = None
    has_fridge: Optional[bool] = None
    has_water_heater: Optional[bool] = None
    has_balcony: Optional[bool] = None
    has_elevator: Optional[bool] = None


# ══════════════════════════════════════════════════════════════════
# TENANT
# ══════════════════════════════════════════════════════════════════
class TenantOut(BaseModel):
    tenant_id: int
    user_id: Optional[int]
    full_name: str
    phone: Optional[str]
    email: Optional[str]
    id_number: Optional[str]
    address: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class TenantCreate(BaseModel):
    full_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    id_number: Optional[str] = None
    address: Optional[str] = None
    user_id: Optional[int] = None


class TenantUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    id_number: Optional[str] = None
    address: Optional[str] = None


# ══════════════════════════════════════════════════════════════════
# CONTRACT
# ══════════════════════════════════════════════════════════════════
class ContractOut(BaseModel):
    contract_id: int
    room_id: int
    tenant_id: int
    start_date: date
    end_date: date
    rent_price: float
    deposit: float
    payment_cycle: str
    status: str
    terms: Optional[str]
    digital_signature: Optional[str]
    created_at: datetime
    room: Optional[RoomOut] = None
    tenant: Optional[TenantOut] = None

    class Config:
        from_attributes = True


class ContractCreate(BaseModel):
    room_id: int
    tenant_id: int
    start_date: date
    end_date: date
    rent_price: float
    deposit: float = 0
    payment_cycle: str = "monthly"
    terms: Optional[str] = None


class ContractUpdate(BaseModel):
    end_date: Optional[date] = None
    rent_price: Optional[float] = None
    deposit: Optional[float] = None
    payment_cycle: Optional[str] = None
    status: Optional[str] = None
    terms: Optional[str] = None
    digital_signature: Optional[str] = None


# ══════════════════════════════════════════════════════════════════
# PAYMENT
# ══════════════════════════════════════════════════════════════════
class PaymentOut(BaseModel):
    payment_id: int
    contract_id: int
    period: str
    amount: float
    electricity_old: int
    electricity_new: int
    water_old: int
    water_new: int
    electricity_unit_price: float
    water_unit_price: float
    service_fee: float
    paid_date: Optional[date]
    method: Optional[str]
    status: str
    note: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class PaymentCreate(BaseModel):
    contract_id: int
    period: str  # YYYY-MM
    amount: float
    electricity_old: int = 0
    electricity_new: int = 0
    water_old: int = 0
    water_new: int = 0
    electricity_unit_price: float = 0
    water_unit_price: float = 0
    service_fee: float = 0
    note: Optional[str] = None


class PaymentUpdate(BaseModel):
    amount: Optional[float] = None
    electricity_old: Optional[int] = None
    electricity_new: Optional[int] = None
    water_old: Optional[int] = None
    water_new: Optional[int] = None
    electricity_unit_price: Optional[float] = None
    water_unit_price: Optional[float] = None
    service_fee: Optional[float] = None
    note: Optional[str] = None
    status: Optional[str] = None


class MarkPaidRequest(BaseModel):
    method: str = "momo_qr"


# ══════════════════════════════════════════════════════════════════
# NOTIFICATION
# ══════════════════════════════════════════════════════════════════
class NotificationOut(BaseModel):
    notification_id: int
    sender_id: Optional[int]
    recipient_id: int
    title: str
    message: str
    notification_type: str
    related_entity_type: Optional[str]
    related_entity_id: Optional[int]
    is_read: bool
    is_system: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SendNotificationRequest(BaseModel):
    recipient_id: Optional[int] = None  # None = gửi tất cả
    title: str
    message: str
    notification_type: str = "general"


class SendMessageRequest(BaseModel):
    message: str
    room_id: Optional[int] = None


# ══════════════════════════════════════════════════════════════════
# PRICE SUGGESTION
# ══════════════════════════════════════════════════════════════════
class PriceSuggestionOut(BaseModel):
    suggestion_id: int
    room_id: int
    suggested_price: float
    based_on_count: int
    algo_version: Optional[str]
    score_breakdown: Optional[Any]
    created_at: datetime

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════════════════════════
# AUDIT LOG
# ══════════════════════════════════════════════════════════════════
class AuditLogOut(BaseModel):
    audit_id: int
    actor_user_id: Optional[int]
    entity_name: str
    entity_id: str
    action: str
    old_data: Optional[Any]
    new_data: Optional[Any]
    changed_at: datetime

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════
class DashboardStats(BaseModel):
    total_rooms: int
    occupied_rooms: int
    available_rooms: int
    occupancy_rate: float
    total_tenants: int
    active_contracts: int
    unpaid_payments: int
    unpaid_total: float
    paid_total: float
    contracts_expiring_30d: int


# ══════════════════════════════════════════════════════════════════
# AI CHAT
# ══════════════════════════════════════════════════════════════════
class ChatMessage(BaseModel):
    role: str   # "user" | "model"
    text: str


class AIChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []


class AIChatResponse(BaseModel):
    reply: str
