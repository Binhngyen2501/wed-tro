"""
AI router – chat với AI assistant
User: hỏi về phòng, hợp đồng, hóa đơn
Admin: phân tích vận hành, báo cáo
"""
from __future__ import annotations

import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from dependencies import get_db, get_current_user, require_admin
from models import User, Room, Contract, Payment, Tenant
from schemas import AIChatRequest, AIChatResponse

router = APIRouter()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


def _format_money(amount) -> str:
    try:
        return f"{int(float(amount)):,} VNĐ"
    except Exception:
        return str(amount)


def _call_gemini(api_key: str, system_instruction: str, history: list, prompt: str) -> str:
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        history_for_api = [
            types.Content(role=m.role, parts=[types.Part(text=m.text)])
            for m in history
        ]
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=history_for_api + [prompt],
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3,
            ),
        )
        return response.text
    except ImportError:
        raise HTTPException(503, "Thư viện google-genai chưa được cài đặt")
    except Exception as e:
        raise HTTPException(500, f"Lỗi AI: {str(e)}")


# ── User AI ───────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=AIChatResponse, summary="Chat với AI Trợ lý (User)")
def user_chat(
    body: AIChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    api_key = GEMINI_API_KEY
    if not api_key:
        raise HTTPException(503, "Chưa cấu hình GEMINI_API_KEY")

    # Build context
    tenant = db.execute(select(Tenant).where(Tenant.user_id == current_user.user_id)).scalar_one_or_none()
    contracts = []
    unpaid_payments = []
    if tenant:
        contracts = db.execute(
            select(Contract).where(Contract.tenant_id == tenant.tenant_id)
        ).scalars().all()
        for c in contracts:
            ups = db.execute(
                select(Payment).where(Payment.contract_id == c.contract_id, Payment.status != "paid")
            ).scalars().all()
            unpaid_payments.extend(ups)

    available_rooms = db.execute(
        select(Room).where(Room.status == "available").limit(10)
    ).scalars().all()

    tenant_ctx = "Chưa liên kết" if not tenant else (
        f"Tên: {tenant.full_name}, CMND: {tenant.id_number}, Địa chỉ: {tenant.address}"
    )
    contracts_ctx = "Không có" if not contracts else "\n".join([
        f"HD#{c.contract_id}: Phòng {c.room_id}, Hạn: {c.start_date}→{c.end_date}, "
        f"Giá {_format_money(c.rent_price)}, Trạng thái: {c.status}"
        for c in contracts
    ])
    unpaid_ctx = "Không có" if not unpaid_payments else "\n".join([
        f"HD#{p.contract_id} (Kỳ {p.period}): Nợ {_format_money(p.amount)}"
        for p in unpaid_payments
    ])
    rooms_ctx = "Không có" if not available_rooms else "\n".join([
        f"- Phòng {r.room_code} (Khu {r.khu_vuc}): {float(r.area_m2)}m², "
        f"Tầng {r.tang}, Giá {_format_money(r.current_rent)}"
        for r in available_rooms
    ])

    system_instruction = f"""
Bạn là **Trợ lý AI Người Thuê** của hệ thống quản lý nhà trọ, xưng "Tôi", gọi người dùng là "Quý khách".

**THÔNG TIN NGƯỜI THUÊ:** {tenant_ctx}
**HỢP ĐỒNG ĐANG CÓ:**\n{contracts_ctx}
**HÓA ĐƠN CHƯA THANH TOÁN:**\n{unpaid_ctx}
**PHÒNG TRỐNG:**\n{rooms_ctx}

Nguyên tắc:
1. Chỉ dùng dữ liệu được cung cấp, không bịa.
2. Hướng dẫn thanh toán qua MoMo QR trong mục "Hóa đơn của tôi".
3. Câu hỏi phức tạp về hợp đồng/pháp lý: hướng dẫn liên hệ admin.
4. Trả lời ngắn gọn, markdown rõ ràng.
"""
    reply = _call_gemini(api_key, system_instruction, body.history, body.message)
    return AIChatResponse(reply=reply)


# ── Admin AI ──────────────────────────────────────────────────────────────────

@router.post("/admin-chat", response_model=AIChatResponse, summary="Chat với AI Agent vận hành (Admin)")
def admin_chat(
    body: AIChatRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    api_key = GEMINI_API_KEY
    if not api_key:
        raise HTTPException(503, "Chưa cấu hình GEMINI_API_KEY")

    rooms = db.execute(select(Room)).scalars().all()
    total_rooms = len(rooms)
    occupied_rooms = sum(1 for r in rooms if r.status == "occupied")
    available_rooms = total_rooms - occupied_rooms
    occupancy_rate = round(occupied_rooms / total_rooms * 100) if total_rooms else 0

    unpaid_payments = db.execute(
        select(Payment).where(Payment.status.in_(["unpaid", "overdue"]))
    ).scalars().all()
    unpaid_total = sum(float(p.amount) for p in unpaid_payments)

    paid_total = float(
        db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "paid")
        ).scalar() or 0
    )
    active_contracts = db.execute(
        select(Contract).where(Contract.status == "active")
    ).scalars().all()

    avail_str = ", ".join(r.room_code for r in rooms if r.status == "available")
    debt_details = "\n".join([
        f"HD#{p.contract_id} (Kỳ {p.period}): {_format_money(p.amount)}"
        for p in unpaid_payments[:15]
    ])

    system_instruction = f"""
Bạn là **AI Agent Vận Hành** chuyên nghiệp, hỗ trợ Admin quản lý hệ thống nhà trọ. Xưng "tôi", gọi "Admin".

**TỔNG QUAN HỆ THỐNG:**
- Tổng phòng: **{total_rooms}** (Trống: {available_rooms} | Đang thuê: {occupied_rooms})
- Tỷ lệ lấp đầy: **{occupancy_rate}%**
- Phòng trống: {avail_str or 'Không có'}
- Hợp đồng active: **{len(active_contracts)}**
- Công nợ: **{len(unpaid_payments)} hóa đơn** — Tổng **{_format_money(unpaid_total)}**
- Doanh thu đã thu: **{_format_money(paid_total)}**

**CHI TIẾT CÔNG NỢ (TOP 15):**
{debt_details or 'Không có'}

Luôn đề xuất hành động cụ thể sau mỗi phân tích. Format markdown rõ ràng.
"""
    reply = _call_gemini(api_key, system_instruction, body.history, body.message)
    return AIChatResponse(reply=reply)
