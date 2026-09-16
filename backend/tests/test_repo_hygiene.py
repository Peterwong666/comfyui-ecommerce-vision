"""全仓库密钥与卫生扫描（todolist P2-12 / 风险登记表 R12）。

**为什么写成 pytest，而不是 `.pre-commit-config.yaml`**
----------------------------------------------------
本仓库已有的两道防漂移门禁（`test_web_enum_parity.py`、`test_web_form_fixtures.py`）
全是**纯 pytest**：零额外依赖、不需要 Node、CI 与本地是同一条命令。把扫描写成 pytest
用例，它就能同时顶两道岗 —— 本地 `pytest` 时拦住一次，CI 里再拦一次 ——
而**不需要引入任何新依赖**。（本机没有安装 pre-commit，写一份跑不起来的钩子配置
等于交付一个假东西：它看起来在防，实际什么都没防。）

另外 pre-commit 是**客户端**钩子，`--no-verify` 就能绕过；CI 里的 pytest 绕不过去。
两者叠加才是防线，这里做的是"能直接被 CI 复用的那一半"。
（P2-12 原话要求"全仓库扫描 + pre-commit 密钥检查"。pre-commit 那一半**没有做**，
理由如上：无法在本机验证。本文件是它的等效且更强的一半。）

**断言清单**（每条都能被人为破坏后检出；交付前已逐条做变异验证）
----------------------------------------------------------
| 用例 | 钉住什么 |
|---|---|
| `test_no_env_file_is_tracked` | R1 没有任何 `.env` 类文件被 git 追踪（唯一例外 `.env.example`） |
| `test_no_model_weight_is_tracked` | R2 没有任何模型权重被追踪 |
| `test_no_credential_file_is_tracked` | R3 没有 `*.pem` / `*.key` / `*.p12` / `credentials.json` / `id_rsa*` 被追踪 |
| `test_no_secret_like_string_in_tracked_text` | R4 被追踪的文本里没有"看起来像真密钥"的字符串（4 条子规则） |
| `test_exemptions_are_well_formed` | R4 的豁免必须规则名真实、文件被追踪、理由非空 |
| `test_exemptions_are_live` | R4 的豁免必须是"活的"（压不住命中就得删掉，防化石） |
| `test_exemption_is_narrow` | R4 豁免机制自测：只压 (文件, 规则) 那一格，别的照报 |
| `test_gitignore_keeps_critical_rules` | R5 `.gitignore` 的关键规则仍在，且模型规则**带根锚定** |
| `test_no_tracked_file_is_ignored_by_gitignore` | R5 推广：没有"已被追踪却仍被忽略规则命中"的文件（事故本色） |
| `test_no_oversized_file_is_tracked` | R6 没有被追踪文件 > 5 MiB |
| `test_ci_workflow_exists_and_parses` | R7 CI 工作流存在、可被 YAML 解析、三个 job 都在 |
| `test_ci_workflow_does_not_claim_to_cover_what_it_cannot` | R7 配套：CI 顶部必须写明"不覆盖什么"（不许把 CI 绿读成功能已验） |

**为什么用 `git ls-files` 而不是遍历文件系统**
------------------------------------------
`.gitignore` **不是防线**，两条理由都能在本仓库找到例子：
- `git add -f` 能强行加进暂存区，`.gitignore` 一声不吭（交付前用这条做过变异验证）；
- **已被追踪的文件不再受 `.gitignore` 约束** —— 一旦 `.env` 进过库，之后再往
  `.gitignore` 里加多少条规则，都挡不住它的后续变更。

所以这里断言的对象是"**追踪状态**"，唯一权威来源是 git 索引（`git ls-files`）。

**豁免机制**（本任务最容易做砸的地方）
--------------------------------
R4 的"通用高熵赋值"规则**天然会误报**：占位符、文档示例、lock 文件里的 hash
都可能命中。处理办法不是"跳过整个 `docs/` 目录"（那等于把防线关掉一半，且
"看起来还在防"是最坏的一种失败），而是**按 (文件, 规则) 精确豁免**，且每条豁免
必须写明理由：

    ALLOWLIST: tuple[Exemption, ...] = (...)

三条约束把豁免钉死：
1. **只对那一个文件的、那一条规则生效** —— 不支持通配、不支持目录级豁免。
   机制本身用合成样本物理验证过（见 `test_exemption_is_narrow`）。
2. **必须给理由**，且豁免指向的文件必须真的被追踪（`test_exemptions_are_well_formed`）。
3. **豁免必须是"活的"**：若某条豁免已经压不住任何命中，测试失败，逼你删掉它
   （`test_exemptions_are_live`）—— 防止豁免清单烂成一堆没人敢碰的化石。

**当前 `ALLOWLIST` 为空**，这是实测结论而不是偷懒：写这道门禁时，R4 的 4 条规则
在全部 266 个被追踪文件（全部可 UTF-8 解码，无需跳过二进制）上**命中数均为 0**。
`.env.example` 里的占位符（`CHANGE_ME_IN_ENV`、`minioadmin`）都短于 32 字符阈值；
`deploy/versions.lock` 与 `web/pnpm-lock.yaml` 的 hash 前面也不带
`secret|token|key` 之类的关键词。所以当前不需要任何豁免 —— 但机制留在原地并自带
自测，将来命中时按上面的格式加，别用"跳过某目录"了事。

还有一处刻意的设计：**本文件自己也是被追踪文件**，同样会被 R4 扫到。所以
`test_exemption_is_narrow` 里的合成样本是**运行时拼出来的**（`"hf_" + "A1b2…"`），
文件里不含任何形似密钥的完整字面量 —— 这样门禁就**不需要给自己开豁免**。
否则"给门禁文件豁免密钥规则"会留下一个真实窟窿：那个文件从此可以夹带真密钥而
无人报警。（这个坑是变异验证时真踩到的：见交付报告。）

**有没有放弃的规则？**
------------------
没有。R1–R7 全部实现并生效，R4 的 4 条子规则（AWS / 私钥块 / HF token / 通用高熵）
也一条没删。之所以能做到"零豁免"，是因为通用高熵规则要求**分隔符后紧跟 ≥32 个
字符**，这个阈值把"占位符"与"真密钥"分开了；若把阈值放宽到 16，本仓库会立刻
出现大量误报 —— 那时正确的做法是**重新设计 R4**，而不是往上堆豁免。

（关于 R6 的 5 MiB：写这道门禁时仓库里最大的被追踪文件是
`deploy/schemas/object_info.v0.36.0.json` ≈ 3.6 MiB —— 那是 ComfyUI 节点 schema
的原始快照，属于必须入库的上游证据，所以阈值不能压到 3 MiB 以下；5 MiB 留了约
40% 余量，同时仍拦得住"顺手把数据集 / 产物 / 权重塞进来"这类事故。）

**已知局限**（写出来，免得读者高估这道防线）
------------------------------------
- 只能扫**文本**：不可 UTF-8 解码的文件被跳过（当前 0 个）。真密钥几乎总是文本，
  但这不是数学保证。
- 只能扫**已被追踪**的文件：新建但未 `git add` 的文件看不见，所以它跟 CI 是互补
  关系，不是替代关系。
- 按**行**匹配：跨行拼接出来的密钥扫不到。
- 它拦的是"**误提交**"，不是"**已泄露**"：密钥一旦进过历史，正确处置是**吊销并
  轮换**，删文件不等于撤销泄露。
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

#: backend/tests/test_repo_hygiene.py → 上三级就是仓库根
ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: 被追踪单文件的体积上限，依据见模块 docstring。
MAX_TRACKED_BYTES = 5 * 1024 * 1024

#: `.env` 类文件里唯一的例外：示例文件（不含真实值，且已被 `test_env_example.py` 审查）
ENV_EXAMPLE_BASENAME = ".env.example"

WEIGHT_SUFFIXES = (
    ".safetensors",
    ".ckpt",
    ".pth",
    ".pt",
    ".bin",
    ".gguf",
    ".onnx",
    ".engine",
    ".fp8",
    ".lora",
)

CREDENTIAL_SUFFIXES = (".pem", ".key", ".p12")
CREDENTIAL_BASENAMES = ("credentials.json", "service_account.json")
CREDENTIAL_NAME_PREFIXES = ("id_rsa",)


# ============================================================
# git 访问层
# ============================================================


def _run_git(
    args: Sequence[str], input_text: str | None = None, *, allow_exit_1: bool = False
) -> str:
    """在仓库根执行 git 并返回 stdout。

    `allow_exit_1` 给 `git check-ignore` 用：它"没有任何路径被忽略"时返回 1，
    那是正常结论，不是错误。
    """
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=input_text,
        )
    except FileNotFoundError:
        pytest.skip("环境里没有 git，无法判定文件的「追踪状态」")
    allowed = {0, 1} if allow_exit_1 else {0}
    if proc.returncode not in allowed:
        pytest.fail(f"`{' '.join(cmd)}` 失败（exit {proc.returncode}）：{proc.stderr.strip()}")
    return proc.stdout


@pytest.fixture(scope="module")
def tracked_files() -> tuple[str, ...]:
    """git 索引里的全部被追踪文件（含已 `git add` 但尚未 commit 的）。"""
    return tuple(p for p in _run_git(["ls-files", "-z"]).split("\0") if p)


@pytest.fixture(scope="module")
def index_blob_sizes(tracked_files: tuple[str, ...]) -> dict[str, int]:
    """每个被追踪文件在**索引**里的字节数（不是工作区大小）。

    从索引取而不是 `os.stat`：这道门禁问的是"会不会被提交"，索引才是答案；
    工作区被删掉的文件 `stat` 会直接抛错，而"暂存了、工作区却没有"恰恰是要拦的场景。
    """
    payload = "".join(f":{path}\n" for path in tracked_files)
    lines = _run_git(["cat-file", "--batch-check"], input_text=payload).splitlines()
    if len(lines) != len(tracked_files):
        pytest.fail(
            f"`git cat-file --batch-check` 返回 {len(lines)} 行、输入 {len(tracked_files)} 个路径，"
            "无法一一对应（路径里有换行？）"
        )
    sizes: dict[str, int] = {}
    for path, line in zip(tracked_files, lines, strict=True):
        parts = line.split()
        if len(parts) != 3 or parts[1] != "blob":
            pytest.fail(f"看不懂 git 的输出（路径 {path!r}）：{line!r}")
        sizes[path] = int(parts[2])
    return sizes


def _read_text(path: str) -> str | None:
    """读文本；不可解码或已从工作区消失则返回 None（由调用方跳过）。"""
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


# ============================================================
# R1–R3：按路径/扩展名判定
# ============================================================


def test_no_env_file_is_tracked(tracked_files: tuple[str, ...]) -> None:
    """R1：不得有任何 `.env` 类文件被追踪，唯一例外是 `.env.example`。

    注意这里问的是"追踪状态"：`.gitignore` 里的 `.env` 规则挡不住
    **已经**被追踪的文件，也挡不住 `git add -f`。
    """
    bad = [
        path
        for path in tracked_files
        if Path(path).name.startswith(".env") and Path(path).name != ENV_EXAMPLE_BASENAME
    ]
    assert not bad, (
        f"以下 {len(bad)} 个 `.env` 类文件被 git 追踪：{bad}\n"
        f"处理：`git rm --cached <文件>` 移出索引；若里面是真凭据，"
        f"还要**吊销并轮换**（从工作区删掉不等于撤销泄露，历史里还在）。"
        f"唯一允许入库的是 `{ENV_EXAMPLE_BASENAME}`。"
    )


def test_no_model_weight_is_tracked(tracked_files: tuple[str, ...]) -> None:
    """R2：模型权重一律不入库（体积巨大 + 许可敏感，仓库只存 registry 清单）。"""
    bad = [path for path in tracked_files if path.lower().endswith(WEIGHT_SUFFIXES)]
    assert not bad, (
        f"以下 {len(bad)} 个模型权重文件被 git 追踪：{bad}\n"
        f"处理：`git rm --cached` 移出索引，权重放 autoDL 数据盘（见 deploy/ 文档）；"
        f"仓库里登记到 `engine/model_registry.yaml` 的应是 hash + 来源 + 许可证，不是文件本身。"
    )


def test_no_credential_file_is_tracked(tracked_files: tuple[str, ...]) -> None:
    """R3：私钥与云凭据文件一律不入库。"""
    bad = []
    for path in tracked_files:
        name = Path(path).name
        if (
            path.lower().endswith(CREDENTIAL_SUFFIXES)
            or name in CREDENTIAL_BASENAMES
            or name.startswith(CREDENTIAL_NAME_PREFIXES)
        ):
            bad.append(path)
    assert not bad, (
        f"以下 {len(bad)} 个私钥/凭据文件被 git 追踪：{bad}\n"
        f"处理：`git rm --cached`，并**吊销该凭据**（私钥入库等同于泄露，"
        f"轮换密钥比删文件重要）；确需在仓库里留说明时，只放公钥或指纹。"
    )


# ============================================================
# R4：文本内容扫描（含豁免机制）
# ============================================================


@dataclass(frozen=True)
class SecretRule:
    """一条"像密钥"的判定规则。"""

    key: str
    label: str
    pattern: re.Pattern[str]
    hint: str


@dataclass(frozen=True)
class SecretHit:
    """一处命中。`line_no` 让报错信息能直接定位到行。"""

    path: str
    line_no: int
    rule: str
    matched: str


@dataclass(frozen=True)
class Exemption:
    """一条豁免 = 「这个文件的这条规则不报」。

    刻意只支持精确路径 + 精确规则名：没有通配符，也就没法把整个目录豁免掉。
    """

    path: str
    rule: str
    reason: str


SECRET_RULES: tuple[SecretRule, ...] = (
    SecretRule(
        key="aws-access-key",
        label="AWS Access Key ID",
        pattern=re.compile(r"AKIA[0-9A-Z]{16}"),
        hint="改用 IAM 角色或环境变量注入，不要写进代码；已入库的 key 必须去 IAM 吊销。",
    ),
    SecretRule(
        key="private-key-block",
        label="私钥块头",
        pattern=re.compile(r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        hint="私钥留在部署机（~/.ssh）或密钥服务里；仓库只放公钥/指纹，并轮换该密钥对。",
    ),
    SecretRule(
        key="hf-token",
        label="Hugging Face token",
        pattern=re.compile(r"hf_[A-Za-z0-9]{30,}"),
        hint="token 写进 `.env`（不入库）或用 HF_TOKEN 环境变量；已入库的去 "
        "huggingface.co/settings/tokens 吊销并换新。",
    ),
    SecretRule(
        key="generic-secret-assignment",
        label="疑似高熵密钥赋值",
        pattern=re.compile(
            r"(?i)(secret|password|passwd|token|api[_-]?key|access[_-]?key)"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9/+_-]{32,}"
        ),
        hint="占位符写成 CHANGE_ME/xxx 或引用环境变量；确实需要出现在文档里的示例，"
        "按 (文件, 规则) 加进本文件的 ALLOWLIST 并写明理由。",
    ),
)

_RULES_BY_KEY = {rule.key: rule for rule in SECRET_RULES}

#: 显式豁免清单。**刻意为空** —— 理由见模块 docstring（当前 4 条规则全仓库 0 命中）。
#: 加条目时必须写清"为什么这处命中是安全的"，并保证它是"活的"。
ALLOWLIST: tuple[Exemption, ...] = ()


def scan_text(
    path: str, text: str, exemptions: Sequence[Exemption] = ALLOWLIST
) -> list[SecretHit]:
    """扫一段文本，返回未被豁免的命中。

    刻意做成**不读磁盘的纯函数**：这样豁免机制本身可以用合成样本验证
    （见 `test_exemption_is_narrow`），而不必往仓库里塞一条真密钥。
    """
    exempted = {(item.path, item.rule) for item in exemptions}
    hits: list[SecretHit] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule in SECRET_RULES:
            match = rule.pattern.search(line)
            if match is not None and (path, rule.key) not in exempted:
                hits.append(SecretHit(path, line_no, rule.key, match.group(0)))
    return hits


def _describe_hits(hits: Sequence[SecretHit]) -> str:
    lines = [f"在被追踪的文本里发现 {len(hits)} 处疑似密钥："]
    for hit in hits:
        rule = _RULES_BY_KEY[hit.rule]
        lines.append(f"  - {hit.path}:{hit.line_no}  [{rule.label}] 命中：{hit.matched[:60]}")
    lines.append("按规则处理建议：")
    for key in sorted({hit.rule for hit in hits}):
        rule = _RULES_BY_KEY[key]
        lines.append(f"  - [{rule.label}] {rule.hint}")
    return "\n".join(lines)


def test_no_secret_like_string_in_tracked_text(tracked_files: tuple[str, ...]) -> None:
    """R4：被追踪的文本里不得出现"看起来像真密钥"的字符串。

    扫的对象是索引里的文件（`tracked_files`），但内容读工作区 ——
    对已 `git add` 的文件，两者一致；改了工作区没 `git add` 的情况下，
    这条会按工作区判定（更严的那一侧）。
    """
    hits: list[SecretHit] = []
    for path in tracked_files:
        text = _read_text(path)
        if text is not None:
            hits.extend(scan_text(path, text))
    assert not hits, _describe_hits(hits)


def test_exemptions_are_well_formed(tracked_files: tuple[str, ...]) -> None:
    """豁免不是注释：规则名必须真实存在，文件必须真被追踪，理由必须非空。"""
    for item in ALLOWLIST:
        assert item.rule in _RULES_BY_KEY, (
            f"豁免 {item.path!r} 指向了不存在的规则名 {item.rule!r}；可选：{sorted(_RULES_BY_KEY)}"
        )
        assert item.reason.strip(), (
            f"豁免 {item.path!r} / {item.rule!r} 没写理由 —— 没有理由的豁免＝悄悄删掉规则"
        )
        assert item.path in tracked_files, (
            f"豁免 {item.path!r} 指向的文件不在索引里：路径写错，或该文件已被删除，"
            f"这条豁免已无意义，请删掉。"
        )


def test_exemptions_are_live(tracked_files: tuple[str, ...]) -> None:
    """豁免必须是"活的"：它得真的压住了至少一处命中，否则就是化石，应当删除。

    （ALLOWLIST 为空时这条自然恒真 —— 这是当前状态的**如实**体现，不是被跳过。）
    """
    raw_keys: set[tuple[str, str]] = set()
    for path in tracked_files:
        text = _read_text(path)
        if text is not None:
            raw_keys.update((hit.path, hit.rule) for hit in scan_text(path, text, exemptions=()))
    for item in ALLOWLIST:
        assert (item.path, item.rule) in raw_keys, (
            f"豁免 {item.path!r} / {item.rule!r} 已经压不住任何命中（规则改强了？文件改了？）"
            f"—— 请删掉这条化石豁免，别让清单越长越没人看。"
        )


def test_exemption_is_narrow() -> None:
    """机制自测：豁免的**作用范围**只有 (那一个文件, 那一条规则) 这一格。

    用合成样本而不是真密钥：既验证了"豁免能压住"，也验证了"别的文件、别的规则
    照报" —— 后者才是防"粗豁免"的关键。

    ⚠️ 样本必须在**运行时拼出来**，不能写成一整段字面量：本文件自己也在被追踪，
    所以会被这道门禁扫到。写死字面量的话，门禁就会被自己的测试夹具打红，接着
    唯一"省事"的出路就是给本文件开豁免 —— 那会留下一个真窟窿（本文件从此可以
    夹带真密钥而无人报警）。拼字符串让门禁**不需要给自己开后门**。
    这个坑是变异验证时真踩到的：见交付报告。
    """
    sample_hf = "hf_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"  # 32 位，命中 hf-token 规则
    sample_aws = "AKIA" + "SAMPLEEXAMPLE123"  # AKIA 后 16 位，命中 aws-access-key 规则
    text = f"{sample_hf}\n{sample_aws}\n"
    # 只在这个自测里临时构造一条豁免（不进 ALLOWLIST）
    exemption = Exemption(path="docs/example.md", rule="hf-token", reason="合成样本，仅用于验证机制")

    # 先钉住规则本身仍然会命中 —— 否则下面"被豁免压住"是假绿
    found = {hit.rule for hit in scan_text("docs/example.md", text, exemptions=())}
    assert found == {"aws-access-key", "hf-token"}, (
        f"合成样本没有同时命中两条规则（实际：{sorted(found)}），下面的断言会变成假绿"
    )

    exempted = scan_text("docs/example.md", text, exemptions=(exemption,))
    assert [hit.rule for hit in exempted] == ["aws-access-key"], (
        "豁免应只压住 (docs/example.md, hf-token) 这一格，同一文件里的 AWS 命中必须照报"
    )

    other_file = scan_text("docs/other.md", text, exemptions=(exemption,))
    assert sorted(hit.rule for hit in other_file) == ["aws-access-key", "hf-token"], (
        "豁免是按**文件**生效的：换个文件后 hf-token 必须重新报出来"
    )


# ============================================================
# R5：.gitignore 的规则仍在（且层级写对）
# ============================================================


def test_gitignore_keeps_critical_rules() -> None:
    """R5：关键忽略规则必须存在，且模型规则必须**根锚定**。

    这条钉的是本仓库的真实事故：`.gitignore` 里写的是不带前导斜杠的 `models/`，
    它匹配任意层级的同名目录，于是 `backend/app/models/` 下 8 个 ORM 源文件
    被整个挡在仓库外、从未入库 —— clone 下来后端直接 import 不了。
    所以"存在"不够，必须是 `/models/` 这种**带前导斜杠**的形式。
    """
    gitignore = ROOT / ".gitignore"
    assert gitignore.exists(), (
        "仓库根没有 .gitignore：模型权重与密钥的第一道（虽然可被绕过的）拦截没了"
    )
    entries = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    required = {
        "/models/": "模型权重目录（必须根锚定，见下）",
        "/assets/models/": "素材库里的模型目录（必须根锚定）",
        ".env": "真实环境变量文件（红线）",
        "*.safetensors": "最常见的权重扩展名",
        "node_modules/": "前端依赖目录（体积大且可由 lockfile 重建）",
    }
    missing = [pattern for pattern in required if pattern not in entries]
    assert not missing, (
        "`.gitignore` 缺少以下关键规则：\n"
        + "\n".join(f"  - {pattern}  （{required[pattern]}）" for pattern in missing)
        + "\n防线被删掉的典型原因是「整理文件时顺手清理」，恢复即可；若要改名，"
        "请同时更新本测试，别让门禁与文件长期不一致。"
    )

    # 事故本色：不带前导斜杠的 models 规则（含 `**/` 变体）会吞掉源码目录
    unanchored = sorted(
        pattern
        for pattern in entries
        if re.fullmatch(r"(\*\*/)?(assets/)?models/?", pattern) is not None
    )
    assert not unanchored, (
        f"`.gitignore` 里出现了**未根锚定**的 models 规则：{unanchored}\n"
        f"它会匹配任意层级的 models/ 目录 —— 这正是把 backend/app/models/ 下 8 个 "
        f"ORM 源文件挡在仓库外的那次事故。请改回 `/models/` 与 `/assets/models/`。"
    )


def test_no_tracked_file_is_ignored_by_gitignore(tracked_files: tuple[str, ...]) -> None:
    """R5 的推广：一个已被追踪的文件不该再被忽略规则命中。

    "既在追踪、又被忽略"说明某条规则的**层级写错了**（多半是漏了前导斜杠）。
    这条能直接抓住那次事故的形态，而不必等到"有人真的 clone 下来才发现少了 8 个文件"。

    ⚠️ `--no-index` 不能省，且**必须**写在这里说明为什么：
    不带它时 `git check-ignore` 判定的是"这个路径将来会不会被 add 时忽略"，
    而**已被追踪的文件永远不在此列** —— 于是无论 .gitignore 写成什么样，它都
    一声不吭。本文件交付前用变异验证抓到过这一点：把 `/models/` 改成 `models/`
    之后这条用例照旧全绿，是个"看起来在防、实际永不报警"的假防线。
    加上 `--no-index` 才会退化成纯粹的"按规则逐条匹配"，而变异后它立刻报出
    `backend/app/models/` 下那 8 个 ORM 文件 —— 正是当年事故的原样脚印。
    """
    output = _run_git(
        ["check-ignore", "--no-index", "--stdin", "-z"],
        input_text="\0".join(tracked_files),
        allow_exit_1=True,
    )
    ignored = sorted(path for path in output.split("\0") if path)
    assert not ignored, (
        f"以下 {len(ignored)} 个**已被追踪**的文件仍被 .gitignore 命中：{ignored}\n"
        f"这说明某条忽略规则的作用范围写大了（典型形态：`models/` 漏掉前导斜杠，"
        f"见 `.gitignore` 里那段注释）。请把规则改回根锚定形式。"
    )


# ============================================================
# R6：大文件兜底
# ============================================================


def test_no_oversized_file_is_tracked(index_blob_sizes: dict[str, int]) -> None:
    """R6：不得有 > 5 MiB 的被追踪文件（阈值依据见模块 docstring）。

    模型权重已由 R2 按扩展名拦住；这条兜的是**其它**大文件 ——
    数据集、产物、导出的日志、压缩包等。
    """
    oversized = sorted(
        ((size, path) for path, size in index_blob_sizes.items() if size > MAX_TRACKED_BYTES),
        reverse=True,
    )
    limit_mib = MAX_TRACKED_BYTES / 1024 / 1024
    assert not oversized, (
        f"以下 {len(oversized)} 个被追踪文件超过 {limit_mib:.0f} MiB 上限：\n"
        + "\n".join(f"  - {size / 1024 / 1024:.1f} MiB  {path}" for size, path in oversized)
        + "\n处理：移出仓库（放数据盘 / 对象存储），或在 `项目进展.md` 里说明它是"
        "不可再生的上游证据，并同步上调本文件的阈值与理由 —— 不要默默跳过这条断言。"
    )


# ============================================================
# R7：CI 工作流本身
# ============================================================


def test_ci_workflow_exists_and_parses() -> None:
    """R7：CI 工作流必须存在且能被 YAML 解析（P2-11 的"真正的流水线"）。

    只用 `yaml.safe_load` 证明**结构可解析**与你声明的 job 都在；
    它**不**证明 GitHub 会接受这份文件 —— runner 行为、action 版本、缓存命中
    都只能在真实 Actions 上验证（本仓库没有远端，这一步未验证）。
    """
    yaml = pytest.importorskip(
        "yaml", reason="环境里没有 pyyaml，无法解析 CI 工作流；跳过而不是假装通过"
    )
    assert CI_WORKFLOW.exists(), (
        f"找不到 CI 工作流 {CI_WORKFLOW}：P2-11 要的是「仓库里真的有一条流水线」，"
        f"文档里写「将来直接进 CI 即可」不算交付。"
    )
    document = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"CI 工作流的顶层结构不是映射：{type(document)!r}"

    jobs = document.get("jobs")
    assert isinstance(jobs, dict) and jobs, f"CI 工作流里没有 jobs：{document!r}"
    for name in ("backend", "engine", "frontend"):
        assert name in jobs, f"CI 里缺少 `{name}` job（现有：{sorted(jobs)}）"

    # YAML 1.1 会把裸写的 `on:` 解析成布尔 True（PyYAML 的已知行为），
    # 所以这里两种形态都接受，只在两种都没有时报错。
    assert "on" in document or True in document, "CI 工作流缺少触发条件（`on:`）"


def test_ci_workflow_does_not_claim_to_cover_what_it_cannot() -> None:
    """R7 的配套：工作流顶部必须写明"不覆盖什么"。

    本项目的文档纪律是**不许让读者把"CI 绿"误读成"功能都验过了"**。
    真实出图 / 真实存储 / 真实 PG / golden set 都需要 GPU 或中间件，CI 里没有，
    所以必须有一处显式声明 —— 这条断言就是钉住那处声明不会在后续编辑里被删掉。
    """
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "不覆盖" in text, (
        "`.github/workflows/ci.yml` 里没有「不覆盖 …」的声明。"
        "请保留顶部那段说明：CI 绿 ≠ 功能验过（真实出图 / 存储 / PG / golden set 都不在 CI 里）。"
    )
    for keyword in ("GPU", "golden set"):
        assert keyword in text, f"CI 的「不覆盖」声明里应显式提到 {keyword!r}"
