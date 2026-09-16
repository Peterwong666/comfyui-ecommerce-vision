"""Pydantic Schema：用户（P2-08）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128, description="至少 8 位")


class UserLoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: str
    quota_total: int
    quota_used: int
    quota_remaining: int
    created_at: datetime


class QuotaUpdateIn(BaseModel):
    """管理员配置配额（FR-9.2）。"""

    quota_total: int = Field(ge=0)
