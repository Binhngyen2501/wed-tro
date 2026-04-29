from __future__ import annotations

from typing import Optional

from passlib.context import CryptContext
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models import User

# Dùng pbkdf2_sha256 để ổn định môi trường chạy demo.
# Báo cáo yêu cầu bcrypt hoặc passlib; ở đây dùng passlib với thuật toán mạnh, dễ chạy đa môi trường.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def authenticate_user(db: Session, login_value: str, password: str) -> Optional[User]:
    stmt = select(User).where(
        or_(User.username == login_value, User.email == login_value, User.phone == login_value)
    )
    user = db.execute(stmt).scalar_one_or_none()
    if not user:
        return None
    if user.status != "active":
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
