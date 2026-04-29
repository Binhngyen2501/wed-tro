from __future__ import annotations

import re
from datetime import date


PHONE_PATTERN = re.compile(r"^0\d{9}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_phone(phone: str | None) -> bool:
    if not phone:
        return True
    return bool(PHONE_PATTERN.match(phone.strip()))


def validate_email(email: str | None) -> bool:
    if not email:
        return True
    return bool(EMAIL_PATTERN.match(email.strip()))


def validate_password_strength(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Mật khẩu phải có ít nhất 8 ký tự"
    if not re.search(r"[A-Z]", password):
        return False, "Mật khẩu phải có ít nhất 1 chữ in hoa"
    if not re.search(r"[a-z]", password):
        return False, "Mật khẩu phải có ít nhất 1 chữ thường"
    if not re.search(r"\d", password):
        return False, "Mật khẩu phải có ít nhất 1 chữ số"
    return True, "OK"


def validate_contract_dates(start_date: date, end_date: date) -> tuple[bool, str]:
    if end_date <= start_date:
        return False, "Ngày kết thúc phải lớn hơn ngày bắt đầu"
    return True, "OK"


def validate_meter_reading(old_value: int, new_value: int) -> tuple[bool, str]:
    if new_value < old_value:
        return False, "Chỉ số mới không được nhỏ hơn chỉ số cũ"
    return True, "OK"
