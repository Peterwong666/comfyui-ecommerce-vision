"""认证接口（FR-1.1）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.enums import UserRole
from app.models.event import AuditLog, EventName
from app.models.user import User
from app.schemas.user import TokenOut, UserLoginIn, UserOut, UserRegisterIn
from app.services.events import track

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegisterIn, db: DbSession) -> TokenOut:
    """注册即送额度、**不需要信用卡**（persona 旅程 2 的痛点「怕麻烦、怕收费」）。"""
    exists = db.scalar(select(User).where(User.email == payload.email))
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该邮箱已注册")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=UserRole.USER.value,
        quota_total=settings.default_quota_total,
        quota_used=0,
    )
    db.add(user)
    db.flush()

    track(db, EventName.USER_REGISTER, user_id=user.id)
    db.add(AuditLog(user_id=user.id, action="user.register", target_type="user", target_id=str(user.id)))
    db.commit()

    token = create_access_token(user.id, extra={"role": user.role})
    return TokenOut(access_token=token, expires_in=settings.access_token_expire_minutes * 60)


@router.post("/login", response_model=TokenOut)
def login(payload: UserLoginIn, db: DbSession) -> TokenOut:
    user = db.scalar(select(User).where(User.email == payload.email))
    # 不区分「用户不存在」与「密码错误」，避免账号枚举
    if user is None or not verify_password(payload.password, user.password_hash):
        db.add(AuditLog(action="user.login_failed", target_type="user", detail={"email": payload.email}))
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")

    from datetime import datetime, timezone

    user.last_login_at = datetime.now(timezone.utc)
    track(db, EventName.USER_LOGIN, user_id=user.id)
    db.add(AuditLog(user_id=user.id, action="user.login", target_type="user", target_id=str(user.id)))
    db.commit()

    token = create_access_token(user.id, extra={"role": user.role})
    return TokenOut(access_token=token, expires_in=settings.access_token_expire_minutes * 60)


@router.get("/me", response_model=UserOut, summary="当前用户与配额")
def me(user: CurrentUser) -> User:
    return user
