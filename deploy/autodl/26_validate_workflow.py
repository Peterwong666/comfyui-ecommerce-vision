#!/usr/bin/env python3
# ============================================================
# 26_validate_workflow.py —— 提交前静态校验工作流（省 GPU 机时的关键一招）
#
# 为什么需要：一次出图冒烟要占 GPU 卡几十秒到几分钟，
#   而「节点类型写错 / 入参名写错 / 模型文件名写错 / 连线指向不存在的节点」
#   这类错误**在提交前就能查出来**。把 GPU 机时花在真正的推理上，
#   而不是花在反复试错 JSON 上。
#
# 校验项：
#   ① class_type 是否存在
#   ② 必填入参是否齐备
#   ③ 入参名是否被该节点接受（含 optional）
#   ④ 连线的目标节点是否存在、输出序号是否越界
#   ⑤ 枚举型入参（如 clip_name / sampler_name）的取值是否合法
#
# 用法：
#   python 26_validate_workflow.py <workflow.json> [--object-info FILE]
#   不带 --object-info 时从 http://127.0.0.1:8188/object_info 拉取
# ============================================================
import argparse
import json
import pathlib
import sys
import urllib.request


def get_object_info(path=None):
    if path:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    with urllib.request.urlopen("http://127.0.0.1:8188/object_info", timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow")
    ap.add_argument("--object-info", default=None)
    ap.add_argument("--port", default="8188")
    args = ap.parse_args()

    wf_all = json.loads(pathlib.Path(args.workflow).read_text(encoding="utf-8"))
    wf = {k: v for k, v in wf_all.items() if not k.startswith("_")}

    if args.object_info:
        info = get_object_info(args.object_info)
    else:
        global_url = f"http://127.0.0.1:{args.port}/object_info"
        with urllib.request.urlopen(global_url, timeout=180) as r:
            info = json.loads(r.read().decode("utf-8"))

    errors, warns = [], []

    for nid, node in wf.items():
        ct = node.get("class_type")
        if ct is None:
            errors.append(f"节点 {nid}: 缺少 class_type")
            continue
        if ct not in info:
            errors.append(f"节点 {nid}: class_type '{ct}' 不存在")
            continue

        spec = info[ct].get("input", {})
        required = spec.get("required", {}) or {}
        optional = spec.get("optional", {}) or {}
        accepted = set(required) | set(optional)
        given = node.get("inputs", {}) or {}

        # ② 必填齐备
        for k in required:
            if k not in given:
                errors.append(f"节点 {nid} ({ct}): 缺少必填入参 '{k}'")
        # ③ 入参名是否被接受
        for k in given:
            if k not in accepted:
                errors.append(f"节点 {nid} ({ct}): 入参 '{k}' 不被该节点接受（可选: {sorted(accepted)}）")

        # ④ 连线校验
        for k, v in given.items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                src, out_idx = v[0], v[1]
                if src not in wf:
                    errors.append(f"节点 {nid} ({ct}).{k}: 连线指向不存在的节点 '{src}'")
                else:
                    src_out = info.get(wf[src].get("class_type"), {}).get("output")
                    n_out = len(src_out) if isinstance(src_out, list) else None
                    if n_out is not None and isinstance(out_idx, int) and out_idx >= n_out:
                        errors.append(
                            f"节点 {nid} ({ct}).{k}: 引用 {src} 的输出序号 {out_idx} 越界（该节点只有 {n_out} 个输出）"
                        )

        # ⑤ 枚举取值校验
        for k, spec_v in required.items():
            if k not in given:
                continue
            val = given[k]
            if not isinstance(spec_v, list) or not spec_v:
                continue
            choices = spec_v[0]
            if isinstance(choices, list) and val not in choices:
                errors.append(f"节点 {nid} ({ct}).{k}: 取值 '{val}' 不在允许集合内")
            # 文件型枚举：给出「近似但不等」的候选，便于发现拼写错误
            if isinstance(choices, list) and isinstance(val, str) and val not in choices:
                near = [c for c in choices if isinstance(c, str) and
                        (val.split('.')[0] in c or c.split('.')[0] in val)]
                if near:
                    warns.append(f"节点 {nid} ({ct}).{k}: 是否想用 {near[:3]} ？")

    print(f"[校验] 工作流: {args.workflow}")
    print(f"[校验] 节点数: {len(wf)}  ·  可用节点类总数: {len(info)}")
    print()
    if warns:
        print("— 提示 —")
        for w in warns:
            print("  ⚠ ", w)
        print()
    if errors:
        print("— 错误 —")
        for e in errors:
            print("  ✗ ", e)
        print()
        print("VALIDATE=FAIL")
        return 1
    print("✅ 全部通过：节点类型、入参名、必填项、连线、枚举取值均无问题")
    print("VALIDATE=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
