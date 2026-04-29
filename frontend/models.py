from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (  # type: ignore
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship  # type: ignore

from db import Base  # type: ignore


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    rooms: Mapped[list[Room]] = relationship("Room", back_populates="owner")
    tenant_profile: Mapped[Tenant | None] = relationship("Tenant", back_populates="user", uselist=False)
    audit_logs: Mapped[list[AuditLog]] = relationship("AuditLog", back_populates="actor")
    sent_notifications: Mapped[list[Notification]] = relationship("Notification", foreign_keys="Notification.sender_id", back_populates="sender")
    received_notifications: Mapped[list[Notification]] = relationship("Notification", foreign_keys="Notification.recipient_id", back_populates="recipient")

    __table_args__ = (
        CheckConstraint("role in ('admin', 'user')", name="ck_users_role"),
        CheckConstraint("status in ('active', 'locked')", name="ck_users_status"),
    )


class Room(Base, TimestampMixin):
    __tablename__ = "rooms"

    room_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    room_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    area_m2: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    khu_vuc: Mapped[str] = mapped_column(String(100), nullable=False)
    tang: Mapped[int] = mapped_column(default=1, nullable=False)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_rent: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), default="available", nullable=False)
    has_aircon: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_fridge: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_water_heater: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_balcony: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_elevator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    owner: Mapped[User] = relationship("User", back_populates="rooms")
    contracts: Mapped[list[Contract]] = relationship("Contract", back_populates="room")
    price_suggestions: Mapped[list[PriceSuggestion]] = relationship("PriceSuggestion", back_populates="room")
    images: Mapped[list[RoomImage]] = relationship("RoomImage", back_populates="room")

    __table_args__ = (
        CheckConstraint("status in ('available', 'occupied')", name="ck_rooms_status"),
    )


class RoomImage(Base, TimestampMixin):
    __tablename__ = "room_images"

    image_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.room_id"), nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    room: Mapped[Room] = relationship("Room", back_populates="images")


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    tenant_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    id_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User | None] = relationship("User", back_populates="tenant_profile")
    contracts: Mapped[list[Contract]] = relationship("Contract", back_populates="tenant")


class Contract(Base, TimestampMixin):
    __tablename__ = "contracts"

    contract_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.room_id"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.tenant_id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    rent_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    deposit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    payment_cycle: Mapped[str] = mapped_column(String(20), default="monthly", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    digital_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)

    room: Mapped[Room] = relationship("Room", back_populates="contracts")
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="contracts")
    payments: Mapped[list[Payment]] = relationship("Payment", back_populates="contract")

    __table_args__ = (
        CheckConstraint("status in ('active', 'ended')", name="ck_contracts_status"),
    )


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    payment_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.contract_id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    electricity_old: Mapped[int] = mapped_column(default=0, nullable=False)
    electricity_new: Mapped[int] = mapped_column(default=0, nullable=False)
    water_old: Mapped[int] = mapped_column(default=0, nullable=False)
    water_new: Mapped[int] = mapped_column(default=0, nullable=False)
    electricity_unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    water_unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    service_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unpaid", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    contract: Mapped[Contract] = relationship("Contract", back_populates="payments")

    __table_args__ = (
        UniqueConstraint("contract_id", "period", name="uq_contract_period"),
        CheckConstraint("status in ('paid', 'unpaid', 'overdue', 'pending_verification')", name="ck_payments_status"),
    )


class PriceSuggestion(Base, TimestampMixin):
    __tablename__ = "price_suggestions"

    suggestion_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.room_id"), nullable=False)
    suggested_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    based_on_count: Mapped[int] = mapped_column(default=0, nullable=False)
    algo_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    room: Mapped[Room] = relationship("Room", back_populates="price_suggestions")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    audit_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"), nullable=True)
    entity_name: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    old_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    actor: Mapped[User | None] = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        CheckConstraint("action in ('insert', 'update', 'delete', 'payment')", name="ck_audit_action"),
    )


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    notification_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"), nullable=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), default="general", nullable=False)
    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    sender: Mapped[User | None] = relationship("User", foreign_keys=[sender_id], back_populates="sent_notifications")
    recipient: Mapped[User] = relationship("User", foreign_keys=[recipient_id], back_populates="received_notifications")

    __table_args__ = (
        CheckConstraint("notification_type in ('general', 'payment', 'contract', 'maintenance', 'reminder', 'system', 'message')", name="ck_notifications_type"),
    )
