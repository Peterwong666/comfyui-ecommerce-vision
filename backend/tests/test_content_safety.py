"""内容安全（P6-13）的机制、误报防护、隐私红线与降级语义测试。

这份文件按「每条都能被人为破坏后检出」来写 —— 交付前逐条做过变异验证
（去掉归一化 / 把 hit 改成放行 / 把命中原词写进审计，见交付报告）。

覆盖 10 组要求
--------------
| 组 | 钉住什么 |
|---|---|
| 1 归一化 | 大小写 / 全角 / 插字（空格、点、星号）都折叠成同一规则 |
| 2 **误报防护** | 逼真电商提示词一律不被拒；含 `暴力熊` / `水枪玩具` 两个指定反例 |
| 3 规则 id | 报出的是**具体**规则 id，不是"反正拒绝了" |
| 4 422 契约 | 结构化 detail、`code=CONTENT_BLOCKED`、`message` 不含命中原词 |
| 5 **隐私红线** | warn 审计只含规则 id / hash / 长度 / 阶段 / 字段名，不含原词与全文 |
| 6 params 通道 | `params.prompt` / `params.negative_prompt` 与顶层字段同样受检 |
| 7 批量 | `POST /batches` 同样受检（走的是同一套参数合并） |
| 8 开关 | `CONTENT_SAFETY_ENABLED=false` → 完全不检查（不拦、也不留痕） |
| 9 NSFW 占位 | `NullNsfwDetector` 返回「未知」，**不是「安全」** |
| 10 词表加载 | 缺失/损坏/空表/非法字段 → 显式报错（拒绝启动），绝不静默降级 |

⚠️ 第 9 组是防「空壳看起来是绿的」：本仓库吃过亏（契约里冻结的 `image_adopted`
长期零触发、`AssetOut` 注释写着"取图走受控接口"而接口从未实现）。一个返回「安全」的
空检测器比没有检测器更糟 —— 它会让下游把「未检测」读成「已检测且通过」。
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.event import AuditLog
from app.models.task import Task
from app.services import content_safety
from app.services.content_safety import (
    NsfwStatus,
    NullNsfwDetector,
    Severity,
    check_text,
)

# ============================================================ 辅助


def _submit(client, **body):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/tasks", json={"workflow_name": "t2i_v1", **body})


def _warn_logs(db: Session) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog).where(AuditLog.action == content_safety.WARN_AUDIT_ACTION)
        ).all()
    )


def _submit_batch(client, common_params: dict):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/batches",
        json={
            "workflow_name": "t2i_v1",
            "sku_assets": {"A": [1]},
            "common_params": common_params,
        },
    )


def _write_blocklist(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "blocklist.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ============================================================ 1. 归一化


@pytest.mark.parametrize(
    "text",
    ["nsfw", "NSFW", "Nsfw", "ＮＳＦＷ", "n s f w", "n.s.f.w", "n*s*f*w", "n_s_f_w", "n-s-f-w"],
)
def test_latin_variants_hit_the_same_rule(text: str) -> None:
    """大小写折叠 + 全角→半角 + 去插字符号后都应命中**同一条**规则。

    破坏方式：把 `check_text` 里的 `normalize(text)` 换成原样文本 ——
    除 `nsfw` 外的每个变体都会变红。
    """
    assert [hit.rule_id for hit in check_text(text)] == ["cs-sexual-01"], (
        f"{text!r} 未命中 cs-sexual-01；归一化（NFKC + casefold + 去规避符号）失效了？"
    )


@pytest.mark.parametrize(
    "text",
    ["儿童色情", "儿 童 色 情", "儿童。色情", "儿童、色情", "儿童，色情", "儿童-色情", "儿童_色情"],
)
def test_cjk_variants_hit_the_same_rule(text: str) -> None:
    """中文没有词边界，靠插标点/空格规避同样要被折叠掉。"""
    hits = [hit for hit in check_text(text) if hit.blocks]
    assert [hit.rule_id for hit in hits] == ["cs-child-01"], f"{text!r} 未命中 cs-child-01"


def test_normalize_is_the_thing_that_makes_variants_match() -> None:
    """直接断言归一化函数的输出，而不是只断言"能命中"。

    没有这一条时，"命中"有可能来自某个恰好存在的词条，而归一化本身是坏的。
    """
    assert content_safety.normalize("Ｎ ＳＦＷ") == "nsfw"
    assert content_safety.normalize("S.E.X") == "sex"
    assert content_safety.normalize("Ｃhild   Porn") == "childporn"
    # 刻意不删引号：删了 `child's exclusive` 会变成 `childsexclusive`（见源码注释）
    assert "'" in content_safety.normalize("child's exclusive")


# ============================================================ 2. 误报防护（最重要的一组）

#: 一批**逼真**的电商提示词：商品名 / 材质 / 场景 / 光影 / 平台规范描述。
#: 它们全都不能命中 hit 级规则 —— 误报是这个功能最大的风险。
_REALISTIC_PROMPTS: tuple[str, ...] = (
    # —— 两个指定反例（会误伤的词根：暴力 → 暴力熊；枪 → 水枪） ——
    "暴力熊 潮玩手办 摆件 白底主图，影棚柔光，高清细节",
    "儿童水枪玩具，夏日戏水场景，浅蓝色背景，阳光",
    # —— 儿童大类目（`儿童` 本身绝不能是词条） ——
    "儿童纯棉 T 恤，卡通印花，白底主图，柔和补光",
    "儿童积木玩具，彩色，白底，柔光箱，45 度俯拍",
    "儿童模特展示童装连衣裙，室内自然光",
    # —— 服装 / 面料（`gore`→Gore-Tex，`sex`→unisex，`仿`→仿羊羔绒） ——
    "户外冲锋衣，Gore-Tex 面料，防泼水，雪山背景，冷调硬光",
    "unisex 宽松卫衣，棉质，正面平铺图，自然光",
    "sexy lingerie 内衣模特，暗调摄影，丝绸质感",
    "仿羊羔绒外套，米白色，平铺图，柔光",
    "仿真花束摆件，客厅场景，自然光，浅景深",
    # —— 材质 / 五金（`枪`→枪灰色） ——
    "枪灰色铝合金拉杆箱，摄影棚硬光，金属高光反射",
    "不锈钢保温杯产品图，白色背景，高光反射，锐利边缘",
    "磨砂玻璃香水瓶，渐变紫，45 度俯拍，冷调",
    # —— 场景 / 装修（`裸露`→裸露的砖墙 是正常装修描述） ——
    "裸露的砖墙背景，工业风咖啡馆，暖色射灯",
    "原木质感餐桌，北欧风格，窗边自然光",
    # —— 英文摄影类提示词 ——
    "high quality product photo, white background, studio lighting, soft shadow, 8k",
    "flat lay composition, marble surface, morning light, minimal, commercial photography",
    "portrait of a fashion model, neutral background, editorial lighting",
    # —— 平台规范 / 后处理类描述 ——
    "去掉水印与文字，统一尺寸 800x800，白底，无阴影",
    "画面干净，无多余元素，主体居中，留白充足",
)


@pytest.mark.parametrize("prompt", _REALISTIC_PROMPTS)
def test_realistic_ecommerce_prompts_are_never_blocked(prompt: str) -> None:
    """逼真电商提示词**一律不被拒**。

    只要求 warn 级命中为空不现实（例如 `血腥玛丽` 这类已知误报会 warn），
    所以这里钉的是**不拒绝** —— 也就是误报不会伤到用户的那条线。
    """
    blocking = [hit.rule_id for hit in check_text(prompt) if hit.blocks]
    assert not blocking, f"正常电商提示词被拒了（命中 {blocking}）：{prompt!r}"


def test_the_two_named_false_positive_traps_are_completely_clean() -> None:
    """`暴力熊` / `水枪玩具` 不仅要「不被拒」，而且**一个命中都不该有**。

    这两条是需求里点名的反例：`暴力` 与 `枪` 都是"看起来像红线"的词，
    一旦有人图省事把它们加进词表，这里会立刻变红。
    """
    assert check_text("暴力熊 潮玩手办 摆件 白底主图") == []
    assert check_text("儿童水枪玩具，夏日戏水场景") == []


def test_known_warn_level_false_positive_is_warned_not_blocked() -> None:
    """把已知误报**写进测试**：`血腥玛丽`（鸡尾酒）会命中 violence 规则。

    它是 warn 而不是 hit —— 用户照样提交成功，只在审计里留一条痕。
    这条用例同时说明"我们的词表不是零误报"，避免读者高估这份表。
    """
    hits = check_text("血腥玛丽鸡尾酒，玻璃杯，暗调美食摄影")
    assert [hit.rule_id for hit in hits] == ["cs-violence-01"]
    assert not hits[0].blocks


def test_only_child_rules_may_block() -> None:
    """钉住分级判据：**默认可拒的只有极窄的法律红线**（当前仅 child）。

    若有人把 brand/sexual/violence 里的某条改成 hit，这条会红 ——
    那正是"无依据地收紧边界会让用户莫名其妙被拒"的入口。
    """
    blocking_categories = {rule.category for rule in content_safety.RULES if rule.blocks}
    assert blocking_categories == {"child"}, (
        f"出现 hit 级的非 child 类别：{sorted(blocking_categories)}；"
        f"hit 只放法律红线，其余一律 warn"
    )


def test_block_terms_are_long_enough_to_be_unambiguous() -> None:
    """hit 级词条不得短到会成为别的商品名的一部分。

    判据按字符信息量分档：拉丁词要 ≥5 字符，中日韩词要 ≥4 字。
    `暴力` / `枪` / `儿童` / `gore` / `sex` 这类词之所以进不了 hit，正是这一条挡住的。
    """
    for rule in content_safety.RULES:
        if not rule.blocks:
            continue
        for term in rule.terms:
            minimal = 5 if term.isascii() else 4
            assert len(term) >= minimal, (
                f"{rule.id} 的 hit 词条 {term!r} 太短（<{minimal}），误伤风险高"
            )


# ============================================================ 3. 规则 id 被正确报出


def test_block_hit_reports_the_exact_rule_id() -> None:
    """报出的是**具体**规则 id 与类别，不是"命中了反正拒绝"。"""
    hits = [hit for hit in check_text("儿童色情") if hit.blocks]
    assert [hit.rule_id for hit in hits] == ["cs-child-01"]
    assert hits[0].category == "child"
    assert hits[0].severity is Severity.HIT
    assert "儿童" in hits[0].reason  # reason 是给用户看的解释


def test_english_block_rule_reports_its_own_id() -> None:
    hits = [hit for hit in check_text("childporn") if hit.blocks]
    assert [hit.rule_id for hit in hits] == ["cs-child-02"]


def test_a_text_can_hit_both_a_block_rule_and_a_warn_rule() -> None:
    """`儿童色情` 同时命中 child(hit) 与 sexual(warn) —— 两条都要报出来。

    这是"规则 id 被正确报出"的边界：只报第一条会让审计丢掉 sexual 那一层。
    """
    hits = check_text("儿童色情")
    assert [hit.rule_id for hit in hits] == ["cs-child-01", "cs-sexual-01"]
    assert [hit.blocks for hit in hits] == [True, False]


def test_rule_hit_structurally_carries_no_matched_term() -> None:
    """`RuleHit` 的字段集合被钉死：**不允许**出现"命中的那个词"。

    这是隐私红线的**结构性**保证 —— 对象上没有这个字段，就不可能有调用方顺手把它
    写进审计 / 日志 / 错误体。加字段必须过这一关。
    """
    hit = check_text("儿童色情")[0]
    assert {field.name for field in dataclasses.fields(hit)} == {
        "rule_id",
        "category",
        "severity",
        "reason",
    }
    assert "儿童色情" not in repr(hit)


# ============================================================ 4. 422：结构化 + 不回显原词


def test_blocked_submit_returns_422_with_structured_detail(client) -> None:  # type: ignore[no-untyped-def]
    resp = _submit(client, prompt="儿童色情 白底主图")

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), "契约 §2.2：需要前端分支处理时 detail 必须是对象"
    assert detail["code"] == "CONTENT_BLOCKED"
    assert detail["code"] == content_safety.CONTENT_BLOCKED_CODE
    assert detail["message"].strip()
    assert [field["key"] for field in detail["fields"]] == ["prompt"]
    assert detail["fields"][0]["reason"].strip()


def test_blocked_response_does_not_echo_the_matched_term(client) -> None:  # type: ignore[no-untyped-def]
    """**响应体里不得出现命中的原词**（也不得出现整个提示词）。

    回显等于把词表泄露给攻击者（可以拿去逐条试），同时把用户输入写回响应与日志。
    注意：这里检查的是**词表里的词条**，不是"任何出现在提示词里的字" ——
    `reason` 里说「涉及对儿童的性化描述」是允许的（它告诉用户是哪一类问题），
    只要没把命中的那个词原样吐回来。
    """
    prompt = "请画 儿童色情 场景"
    resp = _submit(client, prompt=prompt)

    assert resp.status_code == 422
    assert prompt not in resp.text, "响应体里出现了整个提示词"

    blocking_terms = [
        term
        for rule in content_safety.RULES
        if rule.blocks
        for term in rule.terms
    ]
    body = content_safety.normalize(resp.text)
    leaked = [term for term in blocking_terms if term in body]
    assert not leaked, f"响应体回显了词表里的词条：{leaked}"


def test_blocked_submit_creates_no_task_and_charges_no_quota(  # type: ignore[no-untyped-def]
    client, db: Session, user
) -> None:
    """被拒绝的提交必须**零副作用**：不建任务、不扣额度。

    额度扣减在同一个事务里、且只由本请求的 commit 落盘；422 直接抛出不 commit ，
    `get_db` 关闭会话时回滚 —— 所以"拒绝"不会顺手把额度吃掉。

    ⚠️ 断言前必须 `rollback()`：本仓库的测试夹具是**单连接**（内存 SQLite +
    `StaticPool`），路由线程里未提交的 UPDATE 对测试线程**可见**（同一条连接），
    直接读会读到 1 —— 那是夹具假象，不是真泄漏（交付时实测确认过）。
    `rollback()` 只丢弃未提交的东西；**真泄漏是 commit 过的，回滚不掉**，
    所以这条用例对真泄漏依然会变红。
    """
    resp = _submit(client, prompt="儿童色情")
    assert resp.status_code == 422

    assert db.scalar(select(func.count()).select_from(Task)) == 0
    db.rollback()
    db.expire_all()
    assert db.get(type(user), user.id).quota_used == 0, "被内容安全拒绝的提交扣了额度"


# ============================================================ 5. warn：放行 + 隐私红线


def test_warn_hit_is_allowed_and_leaves_a_private_audit_trail(  # type: ignore[no-untyped-def]
    client, db: Session, user
) -> None:
    """warn 级命中 → **202 放行**，并写一条只含非敏感信息的审计。

    ⭐ 这是隐私红线（`tracking_plan.md` §1.2：提示词只记长度与 hash）的回归测试。
    """
    prompt = "户外冲锋衣，nsfw 风格，影棚硬光"
    resp = _submit(client, prompt=prompt)

    assert resp.status_code == 202, resp.text
    assert resp.json()["params"]["prompt"] == prompt  # 放行 = 照常落库

    logs = _warn_logs(db)
    assert len(logs) == 1
    log = logs[0]
    assert log.action == "content.warn"
    assert log.target_type == "task"
    assert log.target_id == str(resp.json()["id"])
    assert log.user_id == user.id

    detail = log.detail
    assert set(detail) == {"rules", "text_hash", "text_length", "stage", "field"}
    assert detail["rules"] == ["cs-sexual-01"]
    assert detail["text_length"] == len(prompt)
    assert detail["text_hash"] == content_safety.fingerprint(prompt)
    assert detail["stage"] == "prompt"
    assert detail["field"] == "prompt"

    # 🔴 红线的核心断言：审计里不含命中的原词、也不含提示词全文
    serialized = json.dumps(detail, ensure_ascii=False)
    assert prompt not in serialized, "审计里出现了提示词全文"
    hit_terms = [
        term for rule in content_safety.RULES if rule.id == "cs-sexual-01" for term in rule.terms
    ]
    for term in hit_terms:
        assert term not in serialized, f"审计里出现了命中的原词 {term!r}"
    assert detail["text_hash"] != prompt, "hash 位置放了原文"


def test_warn_audit_hash_cannot_be_reversed_to_the_prompt() -> None:
    """hash 是定长十六进制、且不等于任何形式的原文片段（防止有人"顺手"存明文）。"""
    prompt = "nsfw"
    digest = content_safety.fingerprint(prompt)
    assert len(digest) == 16 and all(c in "0123456789abcdef" for c in digest)
    assert prompt not in digest


def test_clean_prompt_leaves_no_audit_trail(client, db: Session) -> None:  # type: ignore[no-untyped-def]
    """完全干净的提示词不该产生任何 content.warn 记录（否则审计会被噪声淹掉）。"""
    resp = _submit(client, prompt="白色背景，不锈钢保温杯，影棚柔光")
    assert resp.status_code == 202
    assert _warn_logs(db) == []


# ============================================================ 6. params 通道同样受检


def test_params_prompt_channel_is_checked(client) -> None:  # type: ignore[no-untyped-def]
    """`params["prompt"]` 没有任何长度/内容校验，必须与顶层字段同样受检（契约 §3.4）。"""
    resp = client.post(
        "/api/v1/tasks",
        json={"workflow_name": "t2i_v1", "params": {"prompt": "儿童色情"}},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["fields"][0]["key"] == "prompt"


def test_params_negative_prompt_channel_is_checked(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post(
        "/api/v1/tasks",
        json={"workflow_name": "t2i_v1", "params": {"negative_prompt": "childporn"}},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["fields"][0]["key"] == "negative_prompt"


def test_top_level_negative_prompt_is_checked(client) -> None:  # type: ignore[no-untyped-def]
    """顶层 `negative_prompt` 会收敛进 params，同样要被查到（契约 §3.4）。"""
    resp = _submit(client, negative_prompt="childporn")
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["fields"][0]["key"] == "negative_prompt"


def test_both_channels_are_reported_when_both_are_dirty(client) -> None:  # type: ignore[no-untyped-def]
    """两条通道都脏时，两个字段都要出现在 `fields` 里（只报一个会让人改完又被拒）。"""
    resp = _submit(client, prompt="儿童色情", negative_prompt="childporn")
    assert resp.status_code == 422, resp.text
    keys = [field["key"] for field in resp.json()["detail"]["fields"]]
    assert keys == ["prompt", "negative_prompt"]


def test_warn_in_params_channel_is_audited_with_its_own_field_name(  # type: ignore[no-untyped-def]
    client, db: Session
) -> None:
    resp = client.post(
        "/api/v1/tasks",
        json={"workflow_name": "t2i_v1", "params": {"negative_prompt": "nsfw"}},
    )
    assert resp.status_code == 202, resp.text
    logs = _warn_logs(db)
    assert len(logs) == 1
    assert logs[0].detail["field"] == "negative_prompt"


# ============================================================ 7. 批量同样受检


def test_batch_is_blocked_by_the_same_rule(client) -> None:  # type: ignore[no-untyped-def]
    resp = _submit_batch(client, {"prompt": "儿童色情"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "CONTENT_BLOCKED"


def test_blocked_batch_creates_no_task_and_charges_no_quota(  # type: ignore[no-untyped-def]
    client, db: Session, user
) -> None:
    """批量被拒同理：整批不落库、额度一分不扣（`rollback()` 的理由同上一条）。"""
    resp = _submit_batch(client, {"prompt": "儿童色情"})
    assert resp.status_code == 422
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    db.rollback()
    db.expire_all()
    assert db.get(type(user), user.id).quota_used == 0


def test_batch_warn_is_allowed_and_audited_once_on_the_batch(  # type: ignore[no-untyped-def]
    client, db: Session
) -> None:
    """批量 warn → 放行，且审计只写**一条**（挂在 batch 上）。

    逐张写会在一个 500 张的批量里留下 500 条一模一样的记录 —— 那是噪声，不是留痕。
    """
    resp = _submit_batch(client, {"prompt": "nsfw 风格产品图"})
    assert resp.status_code == 202, resp.text

    logs = _warn_logs(db)
    assert len(logs) == 1
    assert logs[0].target_type == "batch"
    assert logs[0].target_id == str(resp.json()["id"])
    assert "nsfw" not in json.dumps(logs[0].detail, ensure_ascii=False)


# ============================================================ 8. 开关：关掉 = 完全不检查


def test_switch_off_lets_blocking_text_through(  # type: ignore[no-untyped-def]
    client, monkeypatch, db: Session
) -> None:
    """`CONTENT_SAFETY_ENABLED=false` 时，**同样的 hit 级文本能提交成功**。

    这条是开关的**行为**证明：不是"改了配置项"就叫开关生效。
    """
    monkeypatch.setattr(settings, "content_safety_enabled", False)

    resp = _submit(client, prompt="儿童色情")
    assert resp.status_code == 202, resp.text
    assert db.get(Task, resp.json()["id"]) is not None


def test_switch_off_writes_no_audit_at_all(client, monkeypatch, db: Session) -> None:  # type: ignore[no-untyped-def]
    """关闭时**连 warn 审计都不写** —— 否则"检查到底发生没发生"说不清。"""
    monkeypatch.setattr(settings, "content_safety_enabled", False)

    resp = _submit(client, prompt="nsfw 风格产品图")
    assert resp.status_code == 202, resp.text
    assert _warn_logs(db) == []


def test_switch_default_is_on() -> None:
    """默认必须是**开**：一个默认关闭的安全开关等于没有。"""
    assert settings.content_safety_enabled is True


def test_switch_is_read_per_request_not_cached_at_import(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """开关在**每次请求**读取：先关后开，行为要跟着变。

    若有人把开关结果缓存在模块级变量里，第二次调用会沿用第一次的判定。
    """
    monkeypatch.setattr(settings, "content_safety_enabled", False)
    assert _submit(client, prompt="儿童色情").status_code == 202

    monkeypatch.setattr(settings, "content_safety_enabled", True)
    assert _submit(client, prompt="儿童色情").status_code == 422


# ============================================================ 9. NSFW：只有占位，没有假检测


def test_null_nsfw_detector_never_reports_safe() -> None:
    """⭐ 把「未实现」钉死：占位检测器**永远不返回 SAFE**。

    任何"让空壳看起来是绿的"的改动（例如为了"让流程走通"改成返回 SAFE）都会让
    这条变红。返回 UNKNOWN 才能让调用方/看板知道"这里没有检测"。
    """
    verdict = NullNsfwDetector().detect(b"\x89PNG\r\n\x1a\n")

    assert verdict is NsfwStatus.UNKNOWN
    assert verdict is not NsfwStatus.SAFE
    assert verdict != NsfwStatus.SAFE
    assert verdict.value != "safe"


def test_nsfw_status_is_three_valued() -> None:
    """三态而不是两态：没有第三态就无法表达"未检测/无法判定"。"""
    assert {status.value for status in NsfwStatus} == {"safe", "unsafe", "unknown"}


def test_null_detector_satisfies_the_extension_point() -> None:
    """占位实现必须满足 Protocol —— 将来接真检测器时替换点是真的存在。"""
    assert isinstance(NullNsfwDetector(), content_safety.NsfwDetector)


def test_content_safety_imports_are_stdlib_only() -> None:
    """**零新依赖是硬要求**：本模块只许 import 标准库。

    这条同时是"没有偷偷引入视觉检测依赖"的机械证明 —— 加个 `import PIL`
    或 `import torch` 会当场变红（`backend/pyproject.toml` 里没有它们）。
    """
    tree = ast.parse(Path(content_safety.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    allowed = {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "hashlib",
        "json",
        "pathlib",
        "typing",
        "unicodedata",
    }
    assert imported <= allowed, f"内容安全模块引入了非标准库依赖：{sorted(imported - allowed)}"


# ============================================================ 10. 词表加载：失败即报错


def test_blocklist_file_exists_at_the_documented_path() -> None:
    assert content_safety.BLOCKLIST_PATH.exists(), (
        "词表不在 `app/data/content_blocklist.json`。"
        "⚠️ 不要放 `assets/`：全仓库没有任何运行时代码会读 `assets/`，"
        "放那儿等于放了个没人读的文件。"
    )


def test_missing_blocklist_refuses_to_load(tmp_path: Path) -> None:
    """词表缺失 → **显式报错**，绝不静默降级为"不检查"。

    静默降级等于安全防线无声消失，而"看起来还在防、实际什么都没防"是本项目最忌讳的
    一种失败。所以这里选定的语义是：**拒绝启动**（模块在 import 期调用同一个函数）。
    """
    with pytest.raises(content_safety.BlocklistError) as excinfo:
        content_safety.load_rules(tmp_path / "does-not-exist.json")
    assert "不存在" in str(excinfo.value)


def test_corrupt_blocklist_refuses_to_load(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(content_safety.BlocklistError) as excinfo:
        content_safety.load_rules(path)
    assert "JSON" in str(excinfo.value)


def test_empty_blocklist_refuses_to_load(tmp_path: Path) -> None:
    """空表是最隐蔽的一种坏：程序照常跑，但什么都不检查。"""
    path = _write_blocklist(tmp_path, {"rules": []})

    with pytest.raises(content_safety.BlocklistError) as excinfo:
        content_safety.load_rules(path)
    assert "没有任何规则" in str(excinfo.value)


@pytest.mark.parametrize(
    ("broken_rule", "keyword"),
    [
        ({"id": "", "category": "child", "severity": "hit", "terms": ["x"], "reason": "r"}, "id"),
        (
            {"id": "x", "category": "typo", "severity": "hit", "terms": ["x"], "reason": "r"},
            "category",
        ),
        (
            {"id": "x", "category": "child", "severity": "block", "terms": ["x"], "reason": "r"},
            "severity",
        ),
        (
            {"id": "x", "category": "child", "severity": "hit", "terms": [], "reason": "r"},
            "terms",
        ),
        (
            {"id": "x", "category": "child", "severity": "hit", "terms": ["x"], "reason": ""},
            "reason",
        ),
    ],
)
def test_illegal_rule_refuses_to_load(tmp_path: Path, broken_rule: dict, keyword: str) -> None:
    """字段非法一律当场报错：**不跳过、不猜**。

    ⚠️ `severity="block"` 这一条特别重要：本功能的 `hit` 就是「硬拒」，
    如果有人按直觉写成 `block`，静默忽略等于那条规则**根本没生效**。
    """
    path = _write_blocklist(tmp_path, {"rules": [broken_rule]})

    with pytest.raises(content_safety.BlocklistError) as excinfo:
        content_safety.load_rules(path)
    assert keyword in str(excinfo.value)


def test_term_that_normalizes_to_nothing_refuses_to_load(tmp_path: Path) -> None:
    """归一化后为空的词条会**匹配任意文本** —— 最危险的一种写错法，必须拒绝。"""
    path = _write_blocklist(
        tmp_path,
        {
            "rules": [
                {
                    "id": "x",
                    "category": "child",
                    "severity": "hit",
                    "terms": [" * . "],
                    "reason": "r",
                }
            ]
        },
    )

    with pytest.raises(content_safety.BlocklistError) as excinfo:
        content_safety.load_rules(path)
    assert "归一化后为空" in str(excinfo.value)


def test_duplicate_rule_ids_refuse_to_load(tmp_path: Path) -> None:
    """重复 id 会让审计无法区分是两条规则中的哪一条。"""
    rule = {"id": "dup", "category": "child", "severity": "hit", "terms": ["危险词"], "reason": "r"}
    path = _write_blocklist(tmp_path, {"rules": [rule, dict(rule)]})

    with pytest.raises(content_safety.BlocklistError) as excinfo:
        content_safety.load_rules(path)
    assert "重复" in str(excinfo.value)


def test_module_level_rules_are_wired_to_the_real_file() -> None:
    """模块级的 `RULES` 就是 `load_rules()` 在真实路径上的结果。

    结合起来看：`load_rules` 失败会抛（上一条），而它在**import 期**被调用
    （模块末尾的 `RULES = load_rules()`）—— 所以词表坏掉时服务**拒绝启动**。
    """
    assert content_safety.RULES
    assert content_safety.load_rules() == content_safety.RULES


def test_load_failure_is_not_swallowed_when_the_path_breaks(  # type: ignore[no-untyped-def]
    monkeypatch, tmp_path: Path
) -> None:
    """把路径指坏后再加载一次，仍然抛 —— 证明失败路径没有被 `except` 吞掉。"""
    monkeypatch.setattr(content_safety, "BLOCKLIST_PATH", tmp_path / "gone.json")

    with pytest.raises(content_safety.BlocklistError):
        content_safety.load_rules()


def test_loaded_rules_declare_both_severities_and_are_used() -> None:
    """词表里两种级别都在用（只有 hit 就没有 warn 留痕，反之则没有防线）。"""
    assert {rule.severity for rule in content_safety.RULES} == {Severity.HIT, Severity.WARN}


def test_every_rule_reason_is_human_readable_and_term_free() -> None:
    """`reason` 是给用户看的：非空、且**不包含**任何词条。

    含了词条就等于把"命中了哪个词"透给用户与日志 —— 隐私红线不允许。
    （写这份测试时真抓到过三条：neg-027/neg-030/neg-031 的原文被抄进了 reason。）
    """
    for rule in content_safety.RULES:
        assert rule.reason.strip()
        normalized = content_safety.normalize(rule.reason)
        for other in content_safety.RULES:
            for term in other.terms:
                assert term not in normalized, (
                    f"{rule.id} 的 reason 里出现了词条 {term!r}（属于 {other.id}）"
                )


def test_category_labels_do_not_leak_any_term() -> None:
    """类别标签会进 422 的 `message`，因此**同样不许含有任何词条**。

    否则"不回显命中的原词"会被标签从后门破掉（例如标签写成「伪造品牌标识」，
    而 `伪造品牌` 正是词表里的一条）。
    """
    for category, label in content_safety.CATEGORY_LABELS.items():
        normalized = content_safety.normalize(label)
        for rule in content_safety.RULES:
            for term in rule.terms:
                assert term not in normalized, (
                    f"类别 {category} 的标签 {label!r} 含词条 {term!r}，"
                    f"会把命中的原词从 422 的 message 里漏出去"
                )
