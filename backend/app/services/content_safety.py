"""输入侧内容安全（P6-13）—— FR-9.4 的**可交付部分**与 `license_matrix.md` §6 的落地。

它做什么
--------
对**用户提交的提示词文本**（`prompt` / `negative_prompt`）做归一化 + 子串匹配，
命中规则后按严重度分流：

| severity | 口径（沿用 `negative.yaml` 的定义） | 本功能的动作 |
|---|---|---|
| `hit` | 出现即判废 | **硬拒**：422 + `code=CONTENT_BLOCKED`；不落库、不派发、不扣额度 |
| `warn` | 影响观感，可重跑 | **放行**：照常提交，另写一条 `AuditLog(action="content.warn")` |

`hit` 只放**法律红线**，且必须满足「不可能出现在正常电商文案里」。理由：**误报是这个
功能最大的风险** —— 无依据地收紧边界会让用户莫名其妙被拒（同类教训见
`app/core/config.py` 里 `max_sku_count` 的注释）。所以 `暴力` / `枪` / `儿童` 这类商品名
里会出现的词**一律不收**（会误伤「暴力熊」「水枪玩具」「儿童服装」）。

它**不**做什么（必须写清楚，否则等于谎报）
----------------------------------------
- **不做 NSFW 视觉检测。** FR-9.4 的原话是「NSFW 过滤开关与策略」；本模块只交付其中
  的**输入侧文本策略与开关**，对**产物图片**的检测 V1 **未实现、也没有假实现**
  （见下方 `NullNsfwDetector`）。不做的原因不是懒：2530 个 ComfyUI 节点类里没有任何
  安全/NSFW 分类节点（快照 `deploy/schemas/object_info.v0.36.0.json`）、
  `engine/model_registry.yaml` 的 6 个模型全是出图用底模/VAE/文本编码器、
  `backend/pyproject.toml` 刻意不引 torch/Pillow/numpy（`services/image_probe.py`
  把「拿几十行确定代码换掉重依赖」写成了成文纪律），而 `license_matrix.md:16` 的铁律
  是「**未知即不用** —— 找不到明确许可证的模型一律不用」。
- 不做**产物侧**拦截：唯一要求产物打分的是 R-33（Could / V2，落在 P8-03）。
- 不做**肖像权**声明勾选（R-43，Should；`license_matrix.md:239` 只说 V1 内置流程，
  无任何交互设计），也不做词表运维后台（P7-09）。

隐私红线（`docs/prd/tracking_plan.md` §1.2 / 本仓库反复强调的一条）
----------------------------------------------------------------
提示词全文可能含商品名、品牌等敏感信息，**只允许记长度与 hash**。因此：

- 审计里只写规则 id / hash / 长度 / 阶段 / 字段名 ——**绝不写命中的原词、也绝不写全文**；
- 422 的 `message` 只说明「是哪一类问题」，**不回显命中的原词**（回显等于把词表泄露给
  攻击者，同时把用户输入写回响应与日志）；
- `RuleHit` **刻意不携带命中的原词**：只要对象上根本没有这个字段，就不可能有调用方
  「顺手」把它写进审计、日志或错误体。这是一条结构性保证，不是纪律要求。

词表落点与加载语义
----------------
`backend/app/data/content_blocklist.json`，用 stdlib `json` 加载。**不用 YAML**
（`backend/pyproject.toml` 里没有 pyyaml，为一份静态表引入依赖不划算），也**不放
`assets/`**（全仓库没有任何运行时代码会读 `assets/` —— 那是提示词素材库，放那儿等于
交付一个没人读的文件）。

⚠️ **加载失败 = 显式报错、拒绝启动**（`BlocklistError` 在 **import 期**抛出，见文件末尾
的模块级 `RULES = load_rules()`）。**绝不静默降级成「不检查」** —— 静默降级意味着安全
防线无声消失，而「看起来还在防、实际什么都没防」是本项目最忌讳的一种失败。
"""

from __future__ import annotations

import enum
import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

#: 词表的**唯一**落点（`app/data/` 而不是 `assets/`，理由见模块 docstring）。
BLOCKLIST_PATH = Path(__file__).resolve().parent.parent / "data" / "content_blocklist.json"

#: 会被检查的参数字段。**改这里等于改检查范围** —— 目前工作流的提示词类入参只有这两个
#: （见 `workflows/registry.yaml` 各工作流的 `param_schema`）。
PROMPT_FIELDS: tuple[str, ...] = ("prompt", "negative_prompt")

#: 命中硬红线时 422 响应体里的 `code`。⚠️ 它**不是** `ErrorType` 的成员 ——
#: `ErrorType` 与 PRD §6.1 的 EX 编号一一对应（契约 §1.5），而「内容命中」不是一条 EX。
#: 因此这里用契约 §2.2 给「非 ErrorType 码」的口径：**大写下划线**。
CONTENT_BLOCKED_CODE = "CONTENT_BLOCKED"

#: warn 的审计 action（`AuditLog.action`）。
WARN_AUDIT_ACTION = "content.warn"

#: 允许出现的类别。**刻意做成白名单**：类别拼错时会当场报错，而不是安静地多出一个
#: 没人认识的类别（它会一路流进审计与错误体）。
CATEGORIES: tuple[str, ...] = ("child", "brand", "sexual", "violence")

#: 给用户看的类别名。
#:
#: ⚠️ 这些标签会进 422 的 `message`，所以**每个标签都不许含有词表里的任何词条** ——
#: 否则等于借标签回显命中的原词（词表 oracle）。`tests/test_content_safety.py` 里有一条
#: 机械断言盯着这件事（`test_category_labels_do_not_leak_any_term`）。
CATEGORY_LABELS: dict[str, str] = {
    "child": "儿童相关的法律红线",
    "brand": "擅用他人商标标识",
    "sexual": "性化或裸露内容",
    "violence": "暴力类内容",
}

#: 归一化时**删掉**的字符：空白 + 常见「插字规避」符号（含全角）。
#:
#: ⚠️ **刻意不删引号**（`'` `"` 与弯引号）。删了会让 `child's exclusive` 变成
#: `childsexclusive` —— 一旦词表里有 `childsex` 这类前缀就会误伤正常文案。引号插字
#: 这种规避方式属于「覆盖不到」的已知局限，好过误报（见词表 `_note` 的局限说明）。
_EVASION_CHARS = (
    " \t\r\n\u3000\u200b"
    ".*_~·•+|/\\^,;:!?()[]{}<>-–—="
    "，。、；：！？（）【】《》「」『』"
)
_EVASION_TABLE = {ord(ch): None for ch in _EVASION_CHARS}


class BlocklistError(RuntimeError):
    """词表缺失/损坏/为空/字段非法。

    **不会被吞掉**：模块在 import 期就调用 `load_rules()`，所以它一旦抛出，
    `import app.services.content_safety` 失败 → 服务起不来 → 明确报错。
    """


class Severity(str, enum.Enum):
    """严重度。取值与 `assets/prompt_lib/negative.yaml` 的 `severity` 同源。

    `HIT` 就是本功能里的「硬拒」（block）：出现即判废。
    `WARN` 是「放行但留痕」。
    """

    HIT = "hit"
    WARN = "warn"

    @property
    def blocks(self) -> bool:
        return self is Severity.HIT


@dataclass(frozen=True)
class Rule:
    """一条词表规则。`terms` 在加载时**已归一化**（写词表时可以直接写自然写法）。"""

    id: str
    category: str
    severity: Severity
    terms: tuple[str, ...]
    reason: str

    @property
    def blocks(self) -> bool:
        return self.severity.blocks


@dataclass(frozen=True)
class RuleHit:
    """一次命中。

    ⚠️ **刻意不带「命中了哪个词」**：见模块 docstring 的隐私红线。只有规则本身的
    非敏感信息（id / 类别 / 严重度 / 给用户看的原因）会离开本模块。
    """

    rule_id: str
    category: str
    severity: Severity
    reason: str

    @property
    def blocks(self) -> bool:
        return self.severity.blocks


# ============================================================ 归一化与匹配


def normalize(text: str) -> str:
    """归一化：NFKC（全角→半角、兼容字符折叠）→ casefold（大小写折叠）→ 去规避符号。

    中文没有词边界，所以这里是**子串**匹配的输入；不引 jieba —— 零新依赖是硬要求。

    能覆盖的变体（见 `tests/test_content_safety.py` 的归一化用例）：
    `NSFW` / `nsfw` / `ＮＳＦＷ` / `n s f w` / `n.s.f.w` / `n*s*f*w` 全部折叠成 `nsfw`。
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return folded.translate(_EVASION_TABLE)


def _parse_rule(raw: Any, index: int) -> Rule:
    """把一条 JSON 记录转成 `Rule`；任何不合法都**立刻报错**（不跳过、不猜）。"""
    where = f"rules[{index}]"
    if not isinstance(raw, dict):
        raise BlocklistError(f"词表 {where} 不是对象：{type(raw).__name__}")
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise BlocklistError(f"词表 {where} 缺少非空字符串 `id`：{raw!r}")
    where = f"{where}（id={rule_id}）"

    category = raw.get("category")
    if category not in CATEGORIES:
        raise BlocklistError(
            f"词表 {where} 的 `category` 非法：{category!r}；允许值 {CATEGORIES}。"
            f"（白名单是为了让类别拼错时当场暴露，而不是安静流入审计与错误体。）"
        )

    severity_raw = raw.get("severity")
    try:
        severity = Severity(severity_raw)
    except ValueError as exc:
        raise BlocklistError(
            f"词表 {where} 的 `severity` 非法：{severity_raw!r}；"
            f"允许值 {[s.value for s in Severity]}。"
            f"⚠️ 只有 hit 会拒绝提交，不要用未定义的级别。"
        ) from exc

    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise BlocklistError(
            f"词表 {where} 缺少非空 `reason`。reason 是给用户看的解释，"
            f"没有它用户只会看到「被拒绝」而不知道为什么（且**不许**回显命中的原词）。"
        )

    raw_terms = raw.get("terms")
    if not isinstance(raw_terms, list) or not raw_terms:
        raise BlocklistError(f"词表 {where} 的 `terms` 必须是非空数组：{raw_terms!r}")

    terms: list[str] = []
    for term in raw_terms:
        if not isinstance(term, str):
            raise BlocklistError(f"词表 {where} 的词条不是字符串：{term!r}")
        normalized = normalize(term)
        if not normalized:
            # 归一化后为空（例如词条只由空白/规避符号组成）会**匹配一切文本**，
            # 是最危险的一种写错法 —— 必须当场拒绝。
            raise BlocklistError(
                f"词表 {where} 的词条 {term!r} 归一化后为空，会匹配任意文本，拒绝加载"
            )
        terms.append(normalized)

    return Rule(
        id=rule_id,
        category=category,
        severity=severity,
        terms=tuple(dict.fromkeys(terms)),  # 去重且保持顺序
        reason=reason,
    )


def load_rules(path: Path | None = None) -> tuple[Rule, ...]:
    """加载并校验词表。

    ⚠️ **失败即抛 `BlocklistError`，绝不返回空表** —— 空表等于「静默不检查」，
    也就是安全防线无声消失。调用方（模块末尾的 `RULES = load_rules()`）在 import 期
    调用它，所以词表坏掉时服务**拒绝启动**。
    """
    target = BLOCKLIST_PATH if path is None else Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BlocklistError(
            f"内容安全词表不存在：{target}。缺了它就无法做任何过滤，"
            f"因此拒绝启动，而不是静默放行一切。"
        ) from exc
    except OSError as exc:
        raise BlocklistError(f"内容安全词表读取失败：{target}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise BlocklistError(f"内容安全词表不是合法 JSON：{target}：{exc}") from exc

    if not isinstance(payload, dict):
        raise BlocklistError(f"内容安全词表的顶层必须是对象：{type(payload).__name__}")

    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise BlocklistError(
            f"内容安全词表里没有任何规则（{target}）。空表 = 静默不检查 = 防线消失，"
            f"因此拒绝启动。若确实要关闭检查，请用配置项 `CONTENT_SAFETY_ENABLED=false`"
            f"（那是一条**显式**、可被审计的关闭）。"
        )

    rules = tuple(_parse_rule(raw, index) for index, raw in enumerate(raw_rules))
    ids = [rule.id for rule in rules]
    duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
    if duplicates:
        raise BlocklistError(f"内容安全词表里有重复的规则 id：{duplicates}")
    return rules


def check_text(text: str | None, rules: Sequence[Rule] | None = None) -> list[RuleHit]:
    """检查一段文本，返回命中（**纯函数**：不碰 DB / 网络 / 文件系统）。

    `rules` 可注入，便于用合成词表测机制本身；默认用模块级加载的 `RULES`。
    规则 id 去重，且保持词表顺序（同一段文本不会为同一条规则报两次）。
    """
    if not text:
        return []
    haystack = normalize(text)
    if not haystack:
        return []

    active = RULES if rules is None else tuple(rules)
    hits: list[RuleHit] = []
    for rule in active:
        if any(term in haystack for term in rule.terms):
            hits.append(
                RuleHit(
                    rule_id=rule.id,
                    category=rule.category,
                    severity=rule.severity,
                    reason=rule.reason,
                )
            )
    return hits


def check_params(
    params: Mapping[str, Any], rules: Sequence[Rule] | None = None
) -> dict[str, list[RuleHit]]:
    """检查 `params` 里的**提示词类字段**（`PROMPT_FIELDS`）。

    返回 `{字段名: 命中列表}`，只包含**有命中**的字段。

    ⚠️ 只检查 `str` 值：工作流的提示词入参都是标量字符串；非字符串（数字、列表）
    由 `_validate_params` 与工作流 Schema 负责，不在这里做猜测性转换。
    """
    result: dict[str, list[RuleHit]] = {}
    for field in PROMPT_FIELDS:
        value = params.get(field)
        if isinstance(value, str):
            hits = check_text(value, rules)
            if hits:
                result[field] = hits
    return result


# ============================================================ 非敏感留痕 / 错误体


def fingerprint(text: str) -> str:
    """文本指纹（sha256 前 16 位十六进制）。

    用于「同一段违规文本再次出现」时能把两条审计关联起来 —— 既不必存全文，
    也无法从 hash 反推出原文。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def warn_detail(field: str, hits: Sequence[RuleHit], text: str) -> dict[str, Any]:
    """构造 warn 的审计 `detail`。

    **只放非敏感信息**：规则 id、hash、长度、阶段、字段名。
    与用户输入本身有关的一切（原词、全文）都不在这里 —— 见模块 docstring 的隐私红线。
    """
    return {
        "rules": sorted({hit.rule_id for hit in hits}),
        "text_hash": fingerprint(text),
        "text_length": len(text),
        "stage": "prompt",
        "field": field,
    }


def blocked_detail(hits_by_field: Mapping[str, Sequence[RuleHit]]) -> dict[str, Any]:
    """构造 422 的结构化 `detail`（契约 §2.2 的「对象形式」）。

    **只取 `blocks=True` 的命中** —— 一段文本可能同时命中 hit 与 warn（例如
    `儿童色情` 还会命中 sexual 规则），但 warn 的解释出现在「已拒绝」的响应里会自相矛盾
    （它明明写着「本次放行」）。过滤放在这里而不是调用方：这是本函数唯一的正确语义。

    **不回显命中的原词**：`message` 只说明是哪一类问题，`fields[].reason` 取规则自带的
    人类可读解释。回显原词等于把词表泄露给攻击者，也等于把用户输入写回响应体。
    """
    labels: list[str] = []
    fields: list[dict[str, str]] = []
    for field, hits in hits_by_field.items():
        for hit in hits:
            if not hit.blocks:
                continue
            label = CATEGORY_LABELS.get(hit.category, hit.category)
            if label not in labels:
                labels.append(label)
            entry = {"key": field, "reason": hit.reason}
            if entry not in fields:
                fields.append(entry)

    message = (
        f"提示词未通过内容安全策略（{'、'.join(labels)}），已拒绝提交。"
        f"请修改提示词后重试；若认为判定有误，请联系管理员。"
    )
    return {"code": CONTENT_BLOCKED_CODE, "message": message, "fields": fields}


# ============================================================ NSFW 视觉检测：只有占位


class NsfwStatus(str, enum.Enum):
    """NSFW 检测的**三态**。第三态是关键（见 `NullNsfwDetector`）。"""

    SAFE = "safe"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"  # 未实现 / 无法判定 —— 与「安全」是两件不同的事


@runtime_checkable
class NsfwDetector(Protocol):
    """NSFW **视觉**检测的扩展点。V1 **没有任何实现**。

    存在的唯一理由是给未来留一个**名字无法被误读**的落点：要接真的检测器（自建分类器 /
    云 API），实现这个 Protocol 并替换 `NullNsfwDetector` 即可。
    """

    def detect(self, image: bytes) -> NsfwStatus:
        """判定一张图片。"""
        ...


class NullNsfwDetector:
    """**本实现不做任何检测**，仅占位。

    > FR-9.4 的 NSFW **视觉**检测在 V1 未实现。原因见
    > `app/services/content_safety.py` 模块 docstring：「不做的原因不是懒」那一节
    > （无安全分类节点 / registry 无分类器 / 不引重依赖 / 未知即不用）。
    > 未实现的替代手段是 `license_matrix.md` §6 指出的**人工核查**。

    ⚠️ `detect()` 永远返回 `NsfwStatus.UNKNOWN`，**永远不返回 `SAFE`**。

    这不是吹毛求疵：本仓库吃过「空壳看起来是绿的」的亏 —— 契约里冻结的埋点事件名
    `image_adopted` 长期零触发、`AssetOut` 的注释写着「取图走受控接口」而那个接口
    从未实现。一个返回「安全」的空检测器比没有检测器更糟：它会让下游（调用方、
    看板、报表）把「未检测」当成「已检测且通过」，从而**让风险隐形**。
    `tests/test_content_safety.py` 里有一条用例把这一点钉死。
    """

    def detect(self, image: bytes) -> NsfwStatus:
        # 入参刻意不参与任何计算 —— 这里没有模型、没有阈值、没有网络调用。
        return NsfwStatus.UNKNOWN


# ============================================================ 词表：import 期加载一次
#
# 加载只发生在这里（一次），且**失败即拒绝启动**：词表缺失/损坏/为空会让
# `import app.services.content_safety` 直接抛 `BlocklistError`，服务起不来。
# 这是刻意的取舍 —— 见模块 docstring 的「加载语义」。
RULES: tuple[Rule, ...] = load_rules()
