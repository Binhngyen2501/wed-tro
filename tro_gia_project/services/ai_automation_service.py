"""
AI Automation Service - Tự động hóa các quy trình quản lý phòng trọ
Sử dụng AI để tự động phát hiện và thực hiện các tác vụ không cần can thiệp trực tiếp
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import Session, joinedload

from models import Payment, Contract, Room, Tenant, User, PriceSuggestion


@dataclass
class AutomationTask:
    """Đại diện cho một tác vụ tự động"""
    task_type: str
    title: str
    description: str
    priority: str  # high, medium, low
    suggested_action: str
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[int] = None
    auto_executable: bool = False
    confidence_score: float = 0.0


class AIAutomationService:
    """Service tự động hóa AI"""

    def __init__(self, db: Session):
        self.db = db

    # ===================== PAYMENT AUTOMATION =====================

    def detect_overdue_payments(self, days_overdue: int = 5) -> List[AutomationTask]:
        """
        Phát hiện các hóa đơn quá hạn
        """
        current_date = date.today()
        cutoff_date = current_date - timedelta(days=days_overdue)

        # Lấy các hóa đơn unpaid có period trong quá khứ
        stmt = (
            select(Payment)
            .options(
                joinedload(Payment.contract).joinedload(Contract.room),
                joinedload(Payment.contract).joinedload(Contract.tenant)
            )
            .where(
                and_(
                    Payment.status == "unpaid",
                    Payment.period < current_date.strftime("%Y-%m")
                )
            )
        )

        overdue_payments = self.db.execute(stmt).scalars().all()
        tasks = []

        for payment in overdue_payments:
            tenant = payment.contract.tenant
            room = payment.contract.room

            days_late = self._calculate_days_overdue(payment.period)

            if days_late >= days_overdue:
                task = AutomationTask(
                    task_type="payment_overdue",
                    title=f"⚠️ Hóa đơn quá hạn {days_late} ngày",
                    description=(
                        f"Người thuê {tenant.full_name if tenant else 'Unknown'} "
                        f"phòng {room.room_code} chưa thanh toán hóa đơn kỳ {payment.period}. "
                        f"Số tiền: {payment.amount:,.0f} VNĐ."
                    ),
                    priority="high" if days_late > 10 else "medium",
                    suggested_action="send_reminder",
                    related_entity_type="payment",
                    related_entity_id=payment.payment_id,
                    auto_executable=True,
                    confidence_score=0.95,
                )
                tasks.append(task)

        return tasks

    def suggest_payment_reminders(self) -> List[AutomationTask]:
        """
        Đề xuất nhắc nhở thanh toán cho các hóa đơn sắp đến hạn
        """
        current_date = date.today()
        current_period = current_date.strftime("%Y-%m")

        # Lấy các hóa đơn unpaid của kỳ hiện tại
        stmt = (
            select(Payment)
            .options(
                joinedload(Payment.contract).joinedload(Contract.room),
                joinedload(Payment.contract).joinedload(Contract.tenant)
            )
            .where(
                and_(
                    Payment.status == "unpaid",
                    Payment.period == current_period,
                )
            )
        )

        upcoming_payments = self.db.execute(stmt).scalars().all()
        tasks = []

        for payment in upcoming_payments:
            tenant = payment.contract.tenant
            room = payment.contract.room

            # Chỉ gợi ý cho những người đã từng thanh toán muộn
            late_history = self._check_late_payment_history(payment.contract_id)

            if late_history:
                task = AutomationTask(
                    task_type="payment_reminder",
                    title=f"⏰ Nhắc nhở thanh toán - Phòng {room.room_code}",
                    description=(
                        f"Người thuê {tenant.full_name if tenant else 'Unknown'} "
                        f"có lịch sử thanh toán muộn. Đề xuất gửi nhắc nhở trước "
                        f"hóa đơn kỳ {payment.period} ({payment.amount:,.0f} VNĐ)."
                    ),
                    priority="medium",
                    suggested_action="send_proactive_reminder",
                    related_entity_type="payment",
                    related_entity_id=payment.payment_id,
                    auto_executable=True,
                    confidence_score=0.8 if late_history else 0.6,
                )
                tasks.append(task)

        return tasks

    def detect_payment_anomalies(self) -> List[AutomationTask]:
        """
        Phát hiện bất thường trong thanh toán (số điện/nước bất thường)
        """
        tasks = []

        # Lấy các hóa đơn gần đây để phân tích
        recent_payments = self.db.execute(
            select(Payment)
            .options(joinedload(Payment.contract).joinedload(Contract.room))
            .order_by(Payment.created_at.desc())
            .limit(100)
        ).scalars().all()

        for payment in recent_payments:
            room = payment.contract.room
            electric_usage = payment.electricity_new - payment.electricity_old
            water_usage = payment.water_new - payment.water_old

            # Kiểm tra mức tiêu thụ bất thường
            anomalies = []

            if electric_usage > 500:  # > 500 kWh/tháng là bất thường
                anomalies.append(f"Điện tiêu thụ cao ({electric_usage} kWh)")

            if water_usage > 50:  # > 50 m³/tháng là bất thường
                anomalies.append(f"Nước tiêu thụ cao ({water_usage} m³)")

            if electric_usage == 0 and payment.electricity_old > 0:
                anomalies.append("Chỉ số điện không tăng (có thể chưa cập nhật)")

            if water_usage == 0 and payment.water_old > 0:
                anomalies.append("Chỉ số nước không tăng (có thể chưa cập nhật)")

            if anomalies:
                task = AutomationTask(
                    task_type="usage_anomaly",
                    title=f"🔍 Phát hiện bất thường - Phòng {room.room_code}",
                    description="; ".join(anomalies) + f". Kỳ: {payment.period}",
                    priority="medium",
                    suggested_action="review_meter_reading",
                    related_entity_type="payment",
                    related_entity_id=payment.payment_id,
                    auto_executable=False,  # Cần admin review
                    confidence_score=0.75,
                )
                tasks.append(task)

        return tasks

    # ===================== CONTRACT AUTOMATION =====================

    def detect_contracts_ending_soon(self, days_threshold: int = 30) -> List[AutomationTask]:
        """
        Phát hiện hợp đồng sắp hết hạn
        """
        current_date = date.today()
        cutoff_date = current_date + timedelta(days=days_threshold)

        stmt = (
            select(Contract)
            .options(
                joinedload(Contract.room),
                joinedload(Contract.tenant)
            )
            .where(
                and_(
                    Contract.status == "active",
                    Contract.end_date <= cutoff_date,
                    Contract.end_date >= current_date,
                )
            )
        )

        ending_contracts = self.db.execute(stmt).scalars().all()
        tasks = []

        for contract in ending_contracts:
            tenant = contract.tenant
            room = contract.room
            days_remaining = (contract.end_date - current_date).days

            task = AutomationTask(
                task_type="contract_ending",
                title=f"📄 Hợp đồng sắp hết hạn - {room.room_code}",
                description=(
                    f"Hợp đồng của {tenant.full_name if tenant else 'Unknown'} "
                    f"phòng {room.room_code} sẽ hết hạn sau {days_remaining} ngày "
                    f"({contract.end_date})."
                ),
                priority="high" if days_remaining <= 7 else "medium",
                suggested_action="notify_contract_renewal",
                related_entity_type="contract",
                related_entity_id=contract.contract_id,
                auto_executable=True,
                confidence_score=0.98,
            )
            tasks.append(task)

        return tasks

    # ===================== ROOM AUTOMATION =====================

    def suggest_room_maintenance(self) -> List[AutomationTask]:
        """
        Đề xuất bảo trì phòng dựa trên thời gian và lịch sử
        """
        tasks = []

        # Phòng đã occupied lâu (> 6 tháng) chưa có ghi chú bảo trì
        six_months_ago = date.today() - timedelta(days=180)

        stmt = (
            select(Contract)
            .options(joinedload(Contract.room), joinedload(Contract.tenant))
            .where(
                and_(
                    Contract.status == "active",
                    Contract.start_date <= six_months_ago,
                )
            )
        )

        long_term_contracts = self.db.execute(stmt).scalars().all()

        for contract in long_term_contracts:
            room = contract.room
            tenant = contract.tenant

            task = AutomationTask(
                task_type="maintenance_suggestion",
                title=f"🔧 Đề xuất bảo trì - Phòng {room.room_code}",
                description=(
                    f"Phòng {room.room_code} đã được thuê từ {contract.start_date} "
                    f"({(date.today() - contract.start_date).days} ngày). "
                    f"Đề xuất kiểm tra và bảo trì định kỳ."
                ),
                priority="low",
                suggested_action="schedule_maintenance_check",
                related_entity_type="room",
                related_entity_id=room.room_id,
                auto_executable=False,
                confidence_score=0.6,
            )
            tasks.append(task)

        return tasks

    def analyze_room_utilization(self) -> List[AutomationTask]:
        """
        Phân tích hiệu suất sử dụng phòng và đề xuất tối ưu
        """
        tasks = []

        # Đếm số phòng theo trạng thái
        room_stats = self.db.execute(
            select(Room.status, func.count(Room.room_id))
            .group_by(Room.status)
        ).all()

        stats_dict = {status: count for status, count in room_stats}
        total_rooms = sum(stats_dict.values())
        available_rooms = stats_dict.get("available", 0)

        # Nếu tỷ lệ trống cao (> 30%), đề xuất marketing
        if total_rooms > 0 and available_rooms / total_rooms > 0.3:
            vacant_ratio = available_rooms / total_rooms * 100
            task = AutomationTask(
                task_type="marketing_suggestion",
                title="📊 Tỷ lệ phòng trống cao",
                description=(
                    f"Hiện có {available_rooms}/{total_rooms} phòng trống "
                    f"({vacant_ratio:.1f}%). Đề xuất chiến dịch marketing "
                    f"hoặc điều chỉnh giá thuê."
                ),
                priority="medium",
                suggested_action="review_pricing_and_marketing",
                auto_executable=False,
                confidence_score=0.85,
            )
            tasks.append(task)

        return tasks

    # ===================== PRICING AUTOMATION =====================

    def analyze_pricing_opportunities(self) -> List[AutomationTask]:
        """
        Phân tích cơ hội điều chỉnh giá thuê
        """
        tasks = []

        # Lấy các đề xuất giá chưa được xem xét
        suggestions = self.db.execute(
            select(PriceSuggestion)
            .options(joinedload(PriceSuggestion.room))
            .order_by(PriceSuggestion.created_at.desc())
            .limit(10)
        ).scalars().all()

        for suggestion in suggestions:
            room = suggestion.room
            current_price = room.current_rent
            suggested_price = suggestion.suggested_price
            price_diff = suggested_price - current_price

            if abs(price_diff) / current_price > 0.1:  # Chênh lệch > 10%
                direction = "tăng" if price_diff > 0 else "giảm"
                task = AutomationTask(
                    task_type="pricing_suggestion",
                    title=f"💰 Đề xuất {direction} giá - Phòng {room.room_code}",
                    description=(
                        f"Phòng {room.room_code}: Giá hiện tại {current_price:,.0f} VNĐ. "
                        f"Đề xuất {direction} lên {suggested_price:,.0f} VNĐ "
                        f"({price_diff/current_price*100:+.1f}%)."
                    ),
                    priority="medium",
                    suggested_action="review_price_suggestion",
                    related_entity_type="room",
                    related_entity_id=room.room_id,
                    auto_executable=False,
                    confidence_score=min(abs(price_diff) / current_price, 0.9),
                )
                tasks.append(task)

        return tasks

    # ===================== COMPREHENSIVE ANALYSIS =====================

    def run_all_automation_checks(self) -> Dict[str, List[AutomationTask]]:
        """
        Chạy tất cả các kiểm tra tự động và trả về danh sách tác vụ theo loại
        """
        return {
            "payment": (
                self.detect_overdue_payments()
                + self.suggest_payment_reminders()
                + self.detect_payment_anomalies()
            ),
            "contract": self.detect_contracts_ending_soon(),
            "room": self.suggest_room_maintenance() + self.analyze_room_utilization(),
            "pricing": self.analyze_pricing_opportunities(),
        }

    def get_high_priority_tasks(self, limit: int = 10) -> List[AutomationTask]:
        """Lấy các tác vụ ưu tiên cao nhất"""
        all_tasks = []
        for tasks in self.run_all_automation_checks().values():
            all_tasks.extend(tasks)

        # Sắp xếp theo priority và confidence
        priority_order = {"high": 0, "medium": 1, "low": 2}
        all_tasks.sort(key=lambda t: (priority_order.get(t.priority, 3), -t.confidence_score))

        return all_tasks[:limit]

    # ===================== HELPER METHODS =====================

    def _calculate_days_overdue(self, period: str) -> int:
        """Tính số ngày quá hạn dựa trên period (YYYY-MM)"""
        try:
            period_date = datetime.strptime(period + "-01", "%Y-%m-%d").date()
            # Giả sử hạn thanh toán là ngày 5 hàng tháng
            due_date = period_date.replace(day=5)
            if due_date < date.today():
                return (date.today() - due_date).days
        except ValueError:
            pass
        return 0

    def _check_late_payment_history(self, contract_id: int) -> bool:
        """Kiểm tra lịch sử thanh toán muộn của hợp đồng"""
        late_payments = self.db.execute(
            select(func.count())
            .where(
                and_(
                    Payment.contract_id == contract_id,
                    or_(
                        Payment.status == "overdue",
                        # Payment có paid_date quá ngày 5 của period
                    )
                )
            )
        ).scalar()
        return (late_payments or 0) > 0


class AIChatAgent:
    """AI Chat Agent để trả lời câu hỏi về hệ thống"""

    def __init__(self, db: Session):
        self.db = db
        self.context = {}

    def analyze_user_query(self, query: str) -> Dict[str, Any]:
        """
        Phân tích câu hỏi người dùng và xác định intent
        """
        query_lower = query.lower()

        intents = {
            "payment_status": ["thanh toán", "hóa đơn", "nợ", "còn nợ", "chưa trả"],
            "contract_info": ["hợp đồng", "thuê", "hết hạn", "gia hạn"],
            "room_availability": ["phòng trống", "còn phòng", "thuê phòng"],
            "maintenance": ["sửa chữa", "hỏng", "bảo trì", "vấn đề"],
            "revenue": ["doanh thu", "thu nhập", "tiền", "báo cáo"],
        }

        detected_intents = []
        for intent, keywords in intents.items():
            if any(kw in query_lower for kw in keywords):
                detected_intents.append(intent)

        return {
            "intents": detected_intents,
            "query": query,
            "requires_data": len(detected_intents) > 0,
        }

    def generate_response(self, query: str, user_role: str = "user") -> str:
        """
        Tạo câu trả lời dựa trên truy vấn
        """
        analysis = self.analyze_user_query(query)

        if not analysis["requires_data"]:
            return (
                "Xin lỗi, tôi chưa hiểu câu hỏi của bạn. "
                "Bạn có thể hỏi về: thanh toán, hợp đồng, phòng trống, "
                "hoặc các vấn đề bảo trì."
            )

        responses = []

        for intent in analysis["intents"]:
            if intent == "payment_status":
                responses.append(self._get_payment_summary())
            elif intent == "contract_info":
                responses.append(self._get_contract_summary())
            elif intent == "room_availability":
                responses.append(self._get_room_availability())
            elif intent == "revenue" and user_role == "admin":
                responses.append(self._get_revenue_summary())

        return "\n\n".join(responses) if responses else "Tôi sẽ giúp bạn tra cứu thông tin."

    def _get_payment_summary(self) -> str:
        """Lấy tóm tắt thanh toán"""
        unpaid_count = self.db.execute(
            select(func.count()).where(Payment.status == "unpaid")
        ).scalar() or 0

        overdue_count = self.db.execute(
            select(func.count()).where(Payment.status == "overdue")
        ).scalar() or 0

        return (
            f"📊 **Tình hình thanh toán:**\n"
            f"- Hóa đơn chưa thanh toán: {unpaid_count}\n"
            f"- Hóa đơn quá hạn: {overdue_count}\n"
            f"Vui lòng kiểm tra mục 'Hóa đơn' để xem chi tiết."
        )

    def _get_contract_summary(self) -> str:
        """Lấy tóm tắt hợp đồng"""
        active_count = self.db.execute(
            select(func.count()).where(Contract.status == "active")
        ).scalar() or 0

        ending_soon = self.db.execute(
            select(func.count())
            .where(
                and_(
                    Contract.status == "active",
                    Contract.end_date <= date.today() + timedelta(days=30),
                )
            )
        ).scalar() or 0

        return (
            f"📄 **Tình hình hợp đồng:**\n"
            f"- Hợp đồng đang active: {active_count}\n"
            f"- Sắp hết hạn (30 ngày): {ending_soon}\n"
            f"Kiểm tra mục 'Hợp đồng' để xem chi tiết."
        )

    def _get_room_availability(self) -> str:
        """Lấy thông tin phòng trống"""
        available_count = self.db.execute(
            select(func.count()).where(Room.status == "available")
        ).scalar() or 0

        total_count = self.db.execute(select(func.count()).select_from(Room)).scalar() or 0

        return (
            f"🏠 **Tình hình phòng:**\n"
            f"- Tổng số phòng: {total_count}\n"
            f"- Phòng trống: {available_count}\n"
            f"- Tỷ lệ lấp đầy: {((total_count - available_count) / total_count * 100):.1f}%"
        )

    def _get_revenue_summary(self) -> str:
        """Lấy tóm tắt doanh thu (chỉ admin)"""
        current_month = date.today().strftime("%Y-%m")

        paid_this_month = self.db.execute(
            select(func.sum(Payment.amount))
            .where(
                and_(
                    Payment.period == current_month,
                    Payment.status == "paid",
                )
            )
        ).scalar() or 0

        pending_this_month = self.db.execute(
            select(func.sum(Payment.amount))
            .where(
                and_(
                    Payment.period == current_month,
                    Payment.status.in_(["unpaid", "pending_verification"]),
                )
            )
        ).scalar() or 0

        return (
            f"💰 **Doanh thu tháng {current_month}:**\n"
            f"- Đã thu: {paid_this_month:,.0f} VNĐ\n"
            f"- Chưa thu: {pending_this_month:,.0f} VNĐ\n"
            f"- Tổng dự kiến: {(paid_this_month + pending_this_month):,.0f} VNĐ"
        )
