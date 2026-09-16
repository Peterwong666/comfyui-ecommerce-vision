"""`.env.example` 与 `Settings` 的**双向**一致性（P2-12）。

**为什么需要这条测试**：原 `.env.example` 的 30 个变量名与 `config.py` 的字段
**一个都对不上**（`APP_ENV` vs `env`、`SECRET_KEY` vs `jwt_secret`、
`S3_ENDPOINT` vs `minio_endpoint` …），而装载用的是 pydantic-settings +
`extra="ignore"` —— **全部被静默忽略**，不报错、不告警。

危害在于它同时打击了两件本该互相兜底的事：
- ADR-004 的部署方式是「配置外置，客户只改 `.env`」，照抄示例等于**一个配置都不生效**；
- 代码层的同类问题会在 import/运行时报错，而配置层只会安静地沿用默认值 ——
  与 `项目进展.md` #21「提交信息说 A、仓库是 B」同源，但**更难发现**。

所以这里断言**双向**一一对应：既防"示例里有假变量"，也防"加了字段却没登记"。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings

# backend/tests/test_env_example.py → 上三级是仓库根
ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

#: 只匹配 `KEY=...`（行首、全大写+下划线）。注释行与空行自然被排除。
_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)


@pytest.fixture(scope="module")
def example_keys() -> set[str]:
    assert ENV_EXAMPLE.exists(), f"找不到 {ENV_EXAMPLE}"
    return set(_ASSIGNMENT.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def settings_keys() -> set[str]:
    return {name.upper() for name in Settings.model_fields}


def test_example_has_no_unknown_variable(example_keys: set[str], settings_keys: set[str]) -> None:
    """示例里的每个变量都必须真的能被读取。

    这一条直接对应那次事故：写了 30 个变量、0 个生效。
    """
    unknown = sorted(example_keys - settings_keys)
    assert not unknown, (
        f"`.env.example` 里有 {len(unknown)} 个变量不是 Settings 的字段，"
        f"会被 extra='ignore' 静默忽略：{unknown}"
    )


def test_every_setting_is_documented(example_keys: set[str], settings_keys: set[str]) -> None:
    """每个 Settings 字段都必须在示例里出现（可读性 vs 可发现性）。

    允许两种处理：给出可照抄的值，或显式标注「勿手工设置」。
    **不允许省略** —— 省略会让读者以为这项不支持配置，
    于是想改的时候只会去改代码（那就破坏了"配置外置"）。
    """
    missing = sorted(settings_keys - example_keys)
    assert not missing, (
        f"以下配置项未登记到 `.env.example`（{len(missing)} 项），读者会以为它不可配置：{missing}"
    )


def test_env_example_covers_all_fields(example_keys: set[str], settings_keys: set[str]) -> None:
    """双向一致（上面两条的合并断言，便于一眼看出总数）。"""
    assert example_keys == settings_keys


def test_example_is_not_a_copy_of_runtime_env() -> None:
    """本文件是**示例**，不得包含真实密钥。

    这里只做一个低成本的哨兵检查：`JWT_SECRET` 必须仍是占位符。
    真实密钥一旦被抄进示例并提交，就等于泄露（且会长期留在 git 历史里）。
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    match = re.search(r"^JWT_SECRET\s*=\s*(.*)$", text, re.MULTILINE)
    assert match is not None, "`.env.example` 必须包含 JWT_SECRET（否则读者不知道要设）"
    assert match.group(1).strip() == "CHANGE_ME_IN_ENV", (
        "`.env.example` 的 JWT_SECRET 必须是占位符，不能是真实密钥"
    )


def test_secrets_are_placeholder_or_empty(example_keys: set[str]) -> None:
    """含密钥语义的项不得在示例里给出像样的默认值。

    `minioadmin` 是 MinIO 的**官方默认账号**，写在示例里可以接受
    （必须改，但不是泄露）；这里只拦住"看起来像真密钥"的长度。
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in ("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        value = re.search(rf"^{key}\s*=\s*(.*)$", text, re.MULTILINE)
        assert value is not None
        # 32 位以上、且不是已知默认值的，基本可以断定是真实凭据
        stripped = value.group(1).split("#")[0].strip()
        assert len(stripped) <= 16 or stripped == "minioadmin", f"{key} 疑似真实凭据"
