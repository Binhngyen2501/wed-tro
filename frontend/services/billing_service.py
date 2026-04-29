from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Contract, Payment
from utils.validators import validate_meter_reading


def calculate_payment_amount(
    *,
    rent_price: Decimal,
    electricity_old: int,
    electricity_new: int,
    electricity_unit_price: Decimal,
    water_old: int,
    water_new: int,
    water_unit_price: Decimal,
    service_fee: Decimal,
) -> Decimal:
    ok_e, msg_e = validate_meter_reading(electricity_old, electricity_new)
    if not ok_e:
        raise ValueError(msg_e)
    ok_w, msg_w = validate_meter_reading(water_old, water_new)
    if not ok_w:
        raise ValueError(msg_w)

    electric_cost = Decimal(electricity_new - electricity_old) * electricity_unit_price
    water_cost = Decimal(water_new - water_old) * water_unit_price
    return rent_price + electric_cost + water_cost + service_fee


def create_or_update_payment(
    db: Session,
    *,
    contract_id: int,
    period: str,
    electricity_old: int,
    electricity_new: int,
    water_old: int,
    water_new: int,
    electricity_unit_price: Decimal,
    water_unit_price: Decimal,
    service_fee: Decimal,
    note: str | None = None,
) -> Payment:
    contract = db.get(Contract, contract_id)
    if not contract:
        raise ValueError("Không tìm thấy hợp đồng")

    amount = calculate_payment_amount(
        rent_price=contract.rent_price,
        electricity_old=electricity_old,
        electricity_new=electricity_new,
        electricity_unit_price=electricity_unit_price,
        water_old=water_old,
        water_new=water_new,
        water_unit_price=water_unit_price,
        service_fee=service_fee,
    )

    stmt = select(Payment).where(Payment.contract_id == contract_id, Payment.period == period)
    payment = db.execute(stmt).scalar_one_or_none()
    if payment is None:
        payment = Payment(contract_id=contract_id, period=period, amount=amount)
        db.add(payment)

    payment.electricity_old = electricity_old
    payment.electricity_new = electricity_new
    payment.water_old = water_old
    payment.water_new = water_new
    payment.electricity_unit_price = electricity_unit_price
    payment.water_unit_price = water_unit_price
    payment.service_fee = service_fee
    payment.amount = amount
    payment.note = note
    db.flush()
    return payment


def mark_payment_paid(db: Session, payment_id: int, method: str = "bank") -> Payment:
    payment = db.get(Payment, payment_id)
    if not payment:
        raise ValueError("Không tìm thấy hóa đơn")
    payment.status = "paid"
    payment.method = method
    payment.paid_date = date.today()
    db.flush()
    return payment


def build_receipt_pdf(db: Session, payment_id: int) -> bytes:
    payment = db.get(Payment, payment_id)
    if not payment:
        raise ValueError("Không tìm thấy hóa đơn")

    contract = payment.contract
    tenant = contract.tenant
    room = contract.room

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 20 * mm
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(20 * mm, y, "PHIẾU THU TIỀN PHÒNG")

    y -= 12 * mm
    pdf.setFont("Helvetica", 11)
    lines = [
        f"Mã hóa đơn: {payment.payment_id}",
        f"Kỳ thanh toán: {payment.period}",
        f"Phòng: {room.room_code}",
        f"Người thuê: {tenant.full_name}",
        f"Tiền thuê: {contract.rent_price:,.0f} VNĐ",
        f"Điện: {payment.electricity_new - payment.electricity_old} kWh",
        f"Nước: {payment.water_new - payment.water_old} m3",
        f"Phí dịch vụ: {payment.service_fee:,.0f} VNĐ",
        f"Tổng tiền: {payment.amount:,.0f} VNĐ",
        f"Trạng thái: {payment.status}",
        f"Ngày thanh toán: {payment.paid_date or 'Chưa thanh toán'}",
        f"Phương thức: {payment.method or '---'}",
    ]
    for line in lines:
        pdf.drawString(20 * mm, y, line)
        y -= 8 * mm

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return buffer.read()
