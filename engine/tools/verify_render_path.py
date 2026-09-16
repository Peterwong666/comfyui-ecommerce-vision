"""`render_path_l4` 的验证工具（`docs/sop/debug_log.md` §2.4 的可执行版本）。

    # 离线预检（不需要 GPU，可先跑）
    python -m engine.tools.verify_render_path --workflow t2i_v1 --dry-run

    # 真实 L4（需要 ComfyUI 在跑 + GPU）
    python -m engine.tools.verify_render_path --workflow t2i_v1 --seed 20260916

---

### 为什么需要这个脚本

`verification: gpu_verified` 与 `render_path_l4` 是**两件事**：

| | 提交的是什么 | 证明力 |
|---|---|---|
| `verification: gpu_verified` | 工作流 JSON **原样**（`07_bench_workflow.py` 等） | 「**节点图**能出图」 |
| `render_path_l4` | `engine/render` **渲染后**的 JSON | 「**我们的渲染器接进去也能出图**」 |

生产走的是渲染路径，中间多了 `targets` 注入、seed 解析、bypass 裁剪、输出前缀覆写。
**「裸提交能出图」推不出「渲染路径能出图」** —— 所以必须单独验。

### 它验什么（4 项，来自 §2.4）

1. **能提交、能出图**：渲染后的 payload 被 ComfyUI 接受并产出图片
2. **确定性**：**显式 seed** 跑两次，产物的**像素数据（IDAT）逐字节一致**
3. **注入真的落到了图上**：显式 seed 写进工作流；多 target 参数（klein 的 `width`/`height`）
   同时落到**两个**节点
4. **元数据闭环**：产物 PNG 里的 `wf_meta.seed` 等于传入的 seed（证明 §7.1 的
   「seed 由渲染器解析并回写」在生产路径上真的生效），且 ComfyUI 自己写的
   `prompt` chunk 仍在

### 它**不**证明什么（别误读）

* 不证明**画质**合格 —— 那要靠 golden set 与人工评审（P5/P8）
* 不证明**性能**达标 —— 那要靠 `07_bench_workflow.py` 的稳态基准
* 不覆盖 G7（提示词权重）那类**语义**问题 —— 见 `debug_log.md` G7

PASS 只说明：**渲染路径打通了，可以把 `render_path_l4` 置 true。**
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from engine.errors import RenderError
from engine.metadata import (
    METADATA_JSON_KEY,
    _iter_chunks,
    build_metadata,
    read_png_metadata,
    read_png_text_chunks,
    write_png_metadata,
)
from engine.registry import Registry
from engine.render import RenderOptions, load_definition, render

#: 需要「同一 seed 两次产物一致」的默认重跑次数
DEFAULT_RUNS = 2


class Check:
    """一条检查结果。`ok=None` 表示"没跑/被跳过"，**不计入通过**。"""

    def __init__(self, name: str, ok: bool | None, detail: str = "") -> None:
        self.name = name
        self.ok = ok
        self.detail = detail


class Reporter:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, name: str, ok: bool | None, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))
        mark = {True: "✅", False: "❌", None: "⏭ "}[ok]
        line = f"  {mark} {name}"
        print(line + (f"\n       {detail}" if detail else ""))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    @property
    def skipped(self) -> list[Check]:
        return [c for c in self.checks if c.ok is None]


# ============================================================ HTTP（标准库，保持零依赖）


class ComfyClient:
    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _json(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def health(self) -> dict[str, Any]:
        return self._json("/system_stats")

    def submit(self, prompt: dict[str, Any]) -> str:
        data = self._json("/prompt", {"prompt": prompt, "client_id": "engine-verify"})
        pid = data.get("prompt_id")
        if not pid:
            raise RuntimeError(f"/prompt 响应缺少 prompt_id: {data}")
        return str(pid)

    def wait(self, prompt_id: str, timeout: float) -> dict[str, Any]:
        started = time.monotonic()
        while True:
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"等待 {prompt_id} 超过 {timeout}s")
            hist = self._json(f"/history/{prompt_id}")
            entry = hist.get(prompt_id)
            if isinstance(entry, dict):
                status = entry.get("status", {}) or {}
                if status.get("status_str") == "error":
                    msgs = [
                        m[1].get("exception_message", "")
                        for m in status.get("messages", [])
                        if isinstance(m, (list, tuple)) and len(m) >= 2
                        and m[0] == "execution_error" and isinstance(m[1], dict)
                    ]
                    raise RuntimeError("执行报错: " + " | ".join(filter(None, msgs)))
                if status.get("completed") is True:
                    return entry
            time.sleep(0.4)

    def fetch_image(self, img: dict[str, Any]) -> bytes:
        q = urllib.parse.urlencode({
            "filename": img["filename"],
            "subfolder": img.get("subfolder", ""),
            "type": img.get("type", "output"),
        })
        with urllib.request.urlopen(f"{self.base}/view?{q}", timeout=self.timeout) as r:
            return r.read()

    def first_image(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        for _nid, out in (entry.get("outputs") or {}).items():
            if isinstance(out, dict):
                for img in out.get("images") or []:
                    if isinstance(img, dict) and img.get("filename"):
                        return img
        return None


# ============================================================ 辅助


def idat_digest(png_bytes: bytes) -> str:
    """只对**像素数据**取摘要。

    不比整个文件：PNG 里的 chunk 顺序、`prompt` chunk 内容都可能因运行而异，
    而我们要验的是「同 seed 出同一张图」，所以只比 IDAT。
    """
    h = hashlib.sha256()
    for _pos, ctype, _length, data in _iter_chunks(png_bytes):
        if ctype == b"IDAT":
            h.update(data)
    return h.hexdigest()


def _field_defaults(entry: Any) -> dict[str, Any]:
    return {f.key: f.default for f in entry.schema}


def _seed_field_key(entry: Any) -> str | None:
    for f in entry.schema:
        if f.type == "seed":
            return f.key
    return None


def _multi_target_fields(entry: Any) -> dict[str, list[str]]:
    """找出「一个参数映射到多个节点」的字段 → {key: [节点ID...]}。"""
    return {f.key: sorted(t.node_id for t in f.targets)
            for f in entry.schema if len(f.targets) > 1}


# ============================================================ 主流程


def run(args: argparse.Namespace) -> int:
    rep = Reporter()
    registry = Registry.load(args.registry)
    entry = registry.require(args.workflow)
    definition = load_definition(registry.definition_path(entry))

    seed_key = _seed_field_key(entry)
    if seed_key is None:
        print(f"[fatal] 工作流 {entry.id} 的 Schema 里没有 type=seed 的字段，无法做确定性验证")
        return 2

    seed = args.seed
    if seed < 0:
        print("[fatal] 必须传**显式** seed（≥0）—— 确定性验证不能用 -1")
        return 2

    params = _field_defaults(entry)
    params.update(args.param or [])
    params[seed_key] = seed

    print(f"=== render_path_l4 验证：{entry.id} (version {entry.version}) ===")
    print(f"启动参数假设: {args.launch_args}（必须与生产一致，否则验证的不是生产的那个配置）")
    print(f"显式 seed: {seed}（字段 '{seed_key}'）")
    print(f"{'DRY-RUN（不提交，不需要 GPU）' if args.dry_run else '真实提交'}")
    print()

    # ---------- 1. 渲染 ----------
    print("— 1. 渲染（engine/render）—")
    try:
        result = render(definition, entry.schema, params,
                        options=RenderOptions(asset_resolver=None))
    except RenderError as exc:
        rep.add("渲染成功", False, str(exc))
        return 1
    for w in result.warnings:
        print(f"  ⚠ {w}")
    rep.add("渲染成功", True, f"节点数 {result.node_count} · seed={result.seed} · "
                             f"裁剪 {list(result.pruned)} · 重连 {list(result.rewired)}")

    if result.warnings:
        # 警告不是失败，但渲染期出现"入参不存在"这类提示时，L4 的结论会被削弱
        rep.add("渲染期无告警", False,
                "渲染产生了告警（见上）。警告本身不阻断，但请先确认它们不是配置漂移")

    # ---------- 2. seed 与多 target 注入（离线可验，dry-run 也做）----------
    print()
    print("— 2. 注入落点（离线可验）—")

    seed_field = entry.schema.get(seed_key)
    seed_targets = [t for t in seed_field.targets]
    landed = all(
        result.workflow.get(t.node_id, {}).get("inputs", {}).get(t.input) == seed
        for t in seed_targets
    )
    rep.add(
        f"显式 seed 已注入 {[t.describe() for t in seed_targets]}",
        landed,
        f"实际值: {[result.workflow[t.node_id]['inputs'].get(t.input) for t in seed_targets]}",
    )

    for key, nodes in _multi_target_fields(entry).items():
        values = {n: result.workflow[n]["inputs"].get(key) for n in nodes}
        consistent = len(set(map(str, values.values()))) == 1
        rep.add(
            f"多 target 参数 '{key}' 在 {nodes} 上取值一致",
            consistent,
            f"实际: {values}（不一致会导致例如潜空间尺寸与 sigmas 不匹配）",
        )

    # ---------- 3. 提交与出图 ----------
    print()
    print("— 3. 提交与出图 —")
    if args.dry_run:
        rep.add("提交并出图", None, "DRY-RUN 跳过（离线环境无 ComfyUI/GPU）")
        rep.add("产物确定性（同 seed 两次 IDAT 一致）", None, "DRY-RUN 跳过")
        rep.add("产物元数据闭环", None, "DRY-RUN 跳过")
        _summary(rep, dry_run=True)
        return 0 if not rep.failed else 1

    client = ComfyClient(args.base_url)
    try:
        stats = client.health()
        dev = (stats.get("devices") or [{}])[0]
        print(f"  引擎: {dev.get('name')} · vram_total="
              f"{(dev.get('vram_total') or 0) / 1024**3:.1f}GiB")
    except (urllib.error.URLError, OSError) as exc:
        rep.add("ComfyUI 可达", False, f"{args.base_url} 连不上: {exc}")
        return 1
    rep.add("ComfyUI 可达", True, args.base_url)

    digests: list[str] = []
    first_png: bytes | None = None
    for i in range(args.runs):
        tag = f"第 {i + 1} 次"
        try:
            pid = client.submit(result.workflow)
            entry_h = client.wait(pid, args.timeout)
        except Exception as exc:  # noqa: BLE001 —— 网络/执行异常都要转成检查失败
            rep.add(f"{tag} 提交并出图", False, str(exc))
            break
        img = client.first_image(entry_h)
        if img is None:
            rep.add(f"{tag} 提交并出图", False, "产物里没有 images")
            break
        png = client.fetch_image(img)
        digests.append(idat_digest(png))
        if first_png is None:
            first_png = png
        rep.add(f"{tag} 提交并出图", True,
                f"{img['filename']} · {len(png)} bytes · IDAT={digests[-1][:16]}…")

    # ---------- 4. 确定性 ----------
    print()
    print("— 4. 产物确定性 —")
    if len(digests) >= 2:
        same = len(set(digests)) == 1
        rep.add("同一显式 seed 两次出图，像素数据逐字节一致", same,
                "一致" if same else f"不一致: {[d[:16] for d in digests]}"
                "（若不一致，说明该工作流不可复现，FR-5.5「用同参数再生成」不成立）")
    else:
        rep.add("产物确定性", None, f"只拿到 {len(digests)} 个产物，无法比较")

    # ---------- 5. 元数据闭环（走生产路径写元数据）----------
    print()
    print("— 5. 产物元数据闭环 —")
    if first_png is None:
        rep.add("元数据闭环", None, "没有产物可检")
        _summary(rep)
        return 1 if rep.failed else 0

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out_file = outdir / f"{entry.id}_seed{seed}.png"
    out_file.write_bytes(first_png)

    meta = build_metadata(
        workflow_id=entry.id,
        workflow_version=entry.version,
        params=result.params,
        seed=result.seed,
        models=entry.models,
        digest_salt=args.hash_salt,
    )
    before_idat = idat_digest(out_file.read_bytes())
    write_png_metadata(out_file, meta)
    after_idat = idat_digest(out_file.read_bytes())
    rep.add("写元数据未改动像素数据", before_idat == after_idat, f"{out_file}")

    read_back = read_png_metadata(out_file)
    rep.add("产物 PNG 可读回 wf_meta", bool(read_back),
            f"workflow_id={read_back.get('workflow_id')} · seed={read_back.get('seed')}")
    rep.add(
        "wf_meta.seed 等于传入的显式 seed",
        read_back.get("seed") == seed,
        f"期望 {seed}，实际 {read_back.get('seed')}"
        "（不相等说明 §7.1 的「渲染器解析并回写 seed」在生产路径上没生效）",
    )

    chunks = read_png_text_chunks(out_file)
    rep.add("ComfyUI 自己写的 prompt chunk 仍保留", "prompt" in chunks,
            "元数据是**追加**，不能覆盖引擎自己写的可复现凭据")
    rep.add("我们的 wf_meta 已写入", METADATA_JSON_KEY in chunks)

    _summary(rep)
    return 1 if rep.failed else 0


def _summary(rep: Reporter, *, dry_run: bool = False) -> None:
    print()
    print("================ 结论 ================")
    passed = sum(1 for c in rep.checks if c.ok is True)
    print(f"通过 {passed} · 失败 {len(rep.failed)} · 跳过 {len(rep.skipped)}")
    if rep.failed:
        print("❌ 未通过 —— **不要把 render_path_l4 置 true**。逐条修掉上面的失败项后重跑。")
    elif dry_run:
        print("✅ DRY-RUN 通过（注入落点已验）。")
        print("   ⚠️ 这只证明**渲染产物结构正确**，不证明能出图。")
        print("   → 去掉 --dry-run 在 GPU 上跑真实的 L4。")
    else:
        print("✅ 全部通过 —— 可以：")
        print(f"   ① 在 workflows/registry.yaml 里把 {'' if True else ''}`render_path_l4` 置 true")
        print("   ② 把 `status` 改为 enabled（两者必须同时满足，registry 会强制校验）")
        print("   ③ 在 changelog 记一条，并对**两个模型**各跑一遍（修 SDXL 不能拿 klein 当代价）")
    print()
    print("⚠️ 本工具**不**证明画质与性能达标 —— 那分别属于 golden set（P5/P8）与稳态基准（#18）。")


# ============================================================ CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m engine.tools.verify_render_path",
        description="验证「渲染路径」L4（debug_log.md §2.4 的可执行版本）",
    )
    ap.add_argument("--workflow", required=True, help="注册表里的工作流 id")
    ap.add_argument("--registry", default=None, help="注册表路径，默认 workflows/registry.yaml")
    ap.add_argument("--seed", type=int, default=20260916,
                    help="**必须显式**（≥0）。确定性验证不能用 -1")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="重复提交次数（默认 2）")
    ap.add_argument("--base-url", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=float, default=600.0, help="单次等待上限（秒）")
    ap.add_argument("--outdir", default="render_l4_out")
    ap.add_argument("--param", action="append", metavar="KEY=JSON",
                    help="覆盖参数，可重复。例：--param width=768")
    ap.add_argument("--hash-salt", default=None,
                    help="提示词摘要盐；不传则摘要标注 salted=false")
    ap.add_argument("--dry-run", action="store_true",
                    help="只渲染并检查注入落点，**不提交**（离线可用）")
    ap.add_argument("--launch-args", default="--highvram",
                    help="仅用于打印，提醒『验证的配置必须与生产一致』")
    args = ap.parse_args(argv)

    if args.param:
        parsed: dict[str, Any] = {}
        for item in args.param:
            if "=" not in item:
                print(f"[fatal] --param 需要 KEY=JSON 形式: {item}")
                return 2
            key, _, raw = item.partition("=")
            try:
                parsed[key] = json.loads(raw)
            except json.JSONDecodeError:
                parsed[key] = raw  # 允许 --param prompt=一只猫 这种裸字符串
        args.param = parsed
    else:
        args.param = {}

    if args.seed < 0:
        print("[fatal] --seed 必须 ≥ 0（-1 是随机，无法验证确定性）")
        return 2

    return run(args)


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["ComfyClient", "Reporter", "idat_digest", "main", "run"]
