"""
MoMo Payment Service - Tích hợp thanh toán MoMo API
Hỗ trợ cả môi trường test (sandbox) và production
"""

from __future__ import annotations

import hashlib
import hmac
import json
import base64
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any
from urllib.parse import urlencode

import requests
from sqlalchemy.orm import Session


class MoMoConfig:
    """Configuration cho MoMo API"""

    # Sandbox/Testing environment
    SANDBOX_ENDPOINT = "https://test-payment.momo.vn/v2/gateway/api"
    PRODUCTION_ENDPOINT = "https://payment.momo.vn/v2/gateway/api"

    # API Paths
    CREATE_PAYMENT = "/create"
    QUERY_STATUS = "/query"
    REFUND = "/refund"
    CONFIRM = "/confirm"

    def __init__(
        self,
        partner_code: str,
        access_key: str,
        secret_key: str,
        is_sandbox: bool = True,
        redirect_url: Optional[str] = None,
        ipn_url: Optional[str] = None,
    ):
        self.partner_code = partner_code
        self.access_key = access_key
        self.secret_key = secret_key
        self.is_sandbox = is_sandbox
        self.redirect_url = redirect_url or ""
        self.ipn_url = ipn_url or ""
        self.endpoint = self.SANDBOX_ENDPOINT if is_sandbox else self.PRODUCTION_ENDPOINT


class MoMoPaymentService:
    """Service xử lý thanh toán MoMo"""

    def __init__(self, config: MoMoConfig):
        self.config = config

    def _generate_signature(self, raw_data: str) -> str:
        """Tạo chữ ký HMAC SHA256"""
        return hmac.new(
            self.config.secret_key.encode("utf-8"),
            raw_data.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _create_request_id(self) -> str:
        """Tạo request ID duy nhất"""
        return str(uuid.uuid4())

    def _create_order_id(self, prefix: str = "MM") -> str:
        """Tạo order ID theo định dạng MoMo"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        return f"{prefix}{timestamp}{unique_id}"

    def create_payment_request(
        self,
        amount: Decimal,
        order_info: str,
        extra_data: Optional[str] = None,
        request_type: str = "captureWallet",
    ) -> Dict[str, Any]:
        """
        Tạo yêu cầu thanh toán MoMo

        Args:
            amount: Số tiền thanh toán (VNĐ)
            order_info: Thông tin đơn hàng
            extra_data: Dữ liệu bổ sung (base64 encoded)
            request_type: Loại yêu cầu (captureWallet, payWithATM, etc.)

        Returns:
            Dict chứa kết quả từ MoMo API
        """
        order_id = self._create_order_id()
        request_id = self._create_request_id()
        amount_int = int(amount)

        # Chuẩn bị dữ liệu để ký
        raw_signature = (
            f"accessKey={self.config.access_key}"
            f"&amount={amount_int}"
            f"&extraData={extra_data or ''}"
            f"&ipnUrl={self.config.ipn_url}"
            f"&orderId={order_id}"
            f"&orderInfo={order_info}"
            f"&partnerCode={self.config.partner_code}"
            f"&redirectUrl={self.config.redirect_url}"
            f"&requestId={request_id}"
            f"&requestType={request_type}"
        )

        signature = self._generate_signature(raw_signature)

        payload = {
            "partnerCode": self.config.partner_code,
            "partnerName": "Tro Gia Management",
            "storeId": self.config.partner_code,
            "requestId": request_id,
            "amount": amount_int,
            "orderId": order_id,
            "orderInfo": order_info,
            "redirectUrl": self.config.redirect_url,
            "ipnUrl": self.config.ipn_url,
            "lang": "vi",
            "extraData": extra_data or "",
            "requestType": request_type,
            "signature": signature,
        }

        try:
            response = requests.post(
                f"{self.config.endpoint}{self.config.CREATE_PAYMENT}",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            result = response.json()
            result["_local_request_id"] = request_id
            result["_local_order_id"] = order_id
            return result

        except requests.RequestException as e:
            return {
                "resultCode": -1,
                "message": f"Network error: {str(e)}",
                "_local_request_id": request_id,
                "_local_order_id": order_id,
            }

    def query_payment_status(self, order_id: str, request_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Kiểm tra trạng thái thanh toán

        Args:
            order_id: Mã đơn hàng
            request_id: Mã yêu cầu (optional)

        Returns:
            Dict chứa thông tin trạng thái thanh toán
        """
        if not request_id:
            request_id = self._create_request_id()

        raw_signature = (
            f"accessKey={self.config.access_key}"
            f"&orderId={order_id}"
            f"&partnerCode={self.config.partner_code}"
            f"&requestId={request_id}"
        )

        signature = self._generate_signature(raw_signature)

        payload = {
            "partnerCode": self.config.partner_code,
            "requestId": request_id,
            "orderId": order_id,
            "lang": "vi",
            "signature": signature,
        }

        try:
            response = requests.post(
                f"{self.config.endpoint}{self.config.QUERY_STATUS}",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            return response.json()

        except requests.RequestException as e:
            return {
                "resultCode": -1,
                "message": f"Network error: {str(e)}",
            }

    def verify_callback_signature(self, callback_data: Dict[str, Any]) -> bool:
        """
        Xác thực chữ ký từ callback IPN

        Args:
            callback_data: Dữ liệu nhận được từ MoMo

        Returns:
            True nếu chữ ký hợp lệ
        """
        # Trích xuất signature từ callback
        received_signature = callback_data.get("signature", "")

        # Tạo raw data để verify (theo thứ tự MoMo yêu cầu)
        raw_data = (
            f"accessKey={self.config.access_key}"
            f"&amount={callback_data.get('amount', '')}"
            f"&extraData={callback_data.get('extraData', '')}"
            f"&message={callback_data.get('message', '')}"
            f"&orderId={callback_data.get('orderId', '')}"
            f"&orderInfo={callback_data.get('orderInfo', '')}"
            f"&orderType={callback_data.get('orderType', '')}"
            f"&partnerCode={callback_data.get('partnerCode', '')}"
            f"&payType={callback_data.get('payType', '')}"
            f"&requestId={callback_data.get('requestId', '')}"
            f"&responseTime={callback_data.get('responseTime', '')}"
            f"&resultCode={callback_data.get('resultCode', '')}"
            f"&transId={callback_data.get('transId', '')}"
        )

        expected_signature = self._generate_signature(raw_data)
        return hmac.compare_digest(received_signature, expected_signature)

    def is_payment_successful(self, result_code: int) -> bool:
        """Kiểm tra mã kết quả thanh toán thành công"""
        return result_code == 0


class MoMoPaymentStore:
    """Lưu trữ thông tin thanh toán MoMo (in-memory với dict, có thể thay bằng DB)"""

    def __init__(self):
        self._payments: Dict[str, Dict[str, Any]] = {}

    def store_payment(
        self,
        payment_id: int,
        order_id: str,
        request_id: str,
        amount: Decimal,
        momo_response: Dict[str, Any],
    ) -> None:
        """Lưu thông tin thanh toán"""
        self._payments[order_id] = {
            "payment_id": payment_id,
            "order_id": order_id,
            "request_id": request_id,
            "amount": amount,
            "momo_response": momo_response,
            "status": "pending",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }

    def get_payment(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Lấy thông tin thanh toán theo order_id"""
        return self._payments.get(order_id)

    def get_payment_by_payment_id(self, payment_id: int) -> Optional[Dict[str, Any]]:
        """Lấy thông tin thanh toán theo payment_id"""
        for payment in self._payments.values():
            if payment.get("payment_id") == payment_id:
                return payment
        return None

    def update_status(self, order_id: str, status: str, momo_data: Optional[Dict] = None) -> None:
        """Cập nhật trạng thái thanh toán"""
        if order_id in self._payments:
            self._payments[order_id]["status"] = status
            self._payments[order_id]["updated_at"] = datetime.now()
            if momo_data:
                self._payments[order_id]["callback_data"] = momo_data


# Singleton instance
momo_payment_store = MoMoPaymentStore()


def get_momo_service_from_env() -> Optional[MoMoPaymentService]:
    """Tạo MoMo service từ environment variables"""
    import os

    partner_code = os.getenv("MOMO_PARTNER_CODE")
    access_key = os.getenv("MOMO_ACCESS_KEY")
    secret_key = os.getenv("MOMO_SECRET_KEY")

    if not all([partner_code, access_key, secret_key]):
        return None

    is_sandbox = os.getenv("MOMO_ENV", "sandbox").lower() == "sandbox"
    redirect_url = os.getenv("MOMO_REDIRECT_URL", "")
    ipn_url = os.getenv("MOMO_IPN_URL", "")

    config = MoMoConfig(
        partner_code=partner_code,
        access_key=access_key,
        secret_key=secret_key,
        is_sandbox=is_sandbox,
        redirect_url=redirect_url,
        ipn_url=ipn_url,
    )

    return MoMoPaymentService(config)


def create_momo_payment_link(
    payment_id: int,
    amount: Decimal,
    room_code: str,
    period: str,
    tenant_name: str,
) -> Dict[str, Any]:
    """
    Helper function để tạo link thanh toán MoMo cho hóa đơn

    Returns:
        Dict với keys: success (bool), pay_url (str), order_id (str), error (str)
    """
    service = get_momo_service_from_env()

    if not service:
        return {
            "success": False,
            "error": "MoMo chưa được cấu hình. Vui lòng kiểm tra file .env",
            "pay_url": None,
            "order_id": None,
        }

    order_info = f"Thanh toan phong {room_code} ky {period}"

    # Extra data chứa payment_id để callback có thể xử lý
    extra_data_dict = {
        "payment_id": payment_id,
        "room_code": room_code,
        "period": period,
        "tenant_name": tenant_name,
    }
    extra_data = base64.b64encode(json.dumps(extra_data_dict).encode()).decode()

    result = service.create_payment_request(
        amount=amount,
        order_info=order_info,
        extra_data=extra_data,
    )

    if result.get("resultCode") == 0:
        # Lưu vào store để tracking
        momo_payment_store.store_payment(
            payment_id=payment_id,
            order_id=result.get("orderId"),
            request_id=result.get("requestId"),
            amount=amount,
            momo_response=result,
        )

        return {
            "success": True,
            "pay_url": result.get("payUrl"),
            "order_id": result.get("orderId"),
            "deeplink": result.get("deeplink"),
            "qr_code_url": result.get("qrCodeUrl"),
            "error": None,
        }
    else:
        return {
            "success": False,
            "error": result.get("message", "Unknown error"),
            "pay_url": None,
            "order_id": None,
        }


def check_momo_payment_status(order_id: str) -> Dict[str, Any]:
    """Kiểm tra trạng thái thanh toán MoMo"""
    service = get_momo_service_from_env()

    if not service:
        return {
            "success": False,
            "error": "MoMo chưa được cấu hình",
            "status": "unknown",
        }

    stored = momo_payment_store.get_payment(order_id)
    request_id = stored.get("request_id") if stored else None

    result = service.query_payment_status(order_id, request_id)

    if result.get("resultCode") == 0:
        result_code = result.get("resultCode")
        status = "success" if service.is_payment_successful(result_code) else "failed"

        # Update store
        momo_payment_store.update_status(order_id, status, result)

        return {
            "success": True,
            "status": status,
            "amount": result.get("amount"),
            "trans_id": result.get("transId"),
            "pay_type": result.get("payType"),
            "result": result,
        }
    else:
        return {
            "success": False,
            "error": result.get("message", "Query failed"),
            "status": "unknown",
        }


def process_momo_callback(callback_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Xử lý callback từ MoMo IPN

    Returns:
        Dict với payment_id, status, và verified
    """
    service = get_momo_service_from_env()

    if not service:
        return {"verified": False, "error": "Service not configured"}

    # Verify signature
    if not service.verify_callback_signature(callback_data):
        return {"verified": False, "error": "Invalid signature"}

    order_id = callback_data.get("orderId")
    result_code = callback_data.get("resultCode")

    # Update store
    status = "success" if service.is_payment_successful(result_code) else "failed"
    momo_payment_store.update_status(order_id, status, callback_data)

    # Decode extra data để lấy payment_id
    payment_id = None
    try:
        extra_data = callback_data.get("extraData", "")
        if extra_data:
            decoded = base64.b64decode(extra_data).decode()
            extra_dict = json.loads(decoded)
            payment_id = extra_dict.get("payment_id")
    except Exception:
        pass

    return {
        "verified": True,
        "payment_id": payment_id,
        "order_id": order_id,
        "status": status,
        "trans_id": callback_data.get("transId"),
        "amount": callback_data.get("amount"),
        "result": callback_data,
    }
