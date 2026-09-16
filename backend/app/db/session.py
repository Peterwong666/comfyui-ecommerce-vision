"""数据库会话管理（P2-08）。

同步 SQLAlchemy 2.0。理由：
- Celery worker 是同步执行模型，共享同一套 ORM 代码更简单；
- 出图任务的瓶颈在 GPU（10s 级），DB 的毫秒级开销可忽略；
- 避免 async SQLAlchemy 在 Celery 中的事件循环陷阱。
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,  # 避免连接被 PG 单方面断开后报错
    echo=settings.db_echo,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Worker / 脚本用：自动提交与回滚。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
