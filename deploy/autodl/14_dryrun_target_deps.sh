#!/usr/bin/env bash
# ============================================================
# 14_dryrun_target_deps.sh —— 预演安装目标版本的依赖（不落地）
#
# 目的：在 checkout 之前先确认「目标版本的依赖能否在本环境装齐」。
#       若此时就发现解不开（例如某个包要求更新的 torch），
#       我们还在 v0.3.75 的完好状态，回退成本为零。
#
# 注意：这里用 `git show v0.36.0:requirements.txt` 取目标依赖，
#       不需要切换工作树 —— 属于「先问清楚再动手」。
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python
PIP=/root/miniconda3/bin/pip

export PIP_NO_CACHE_DIR=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_ROOT_USER_ACTION=ignore

TMPREQ=/root/autodl-tmp/requirements_v036.txt
git show v0.36.0:requirements.txt > "$TMPREQ"
echo "目标 requirements 已取出 -> $TMPREQ ($(wc -l < "$TMPREQ") 行)"
echo

echo "############ 0. pip / python ############"
$PIP -V
$PY -V
echo

echo "############ 1. 目标 requirements 全文 ############"
cat "$TMPREQ"
echo

echo "############ 2. dry-run 解析（--dry-run --report 只解析不落地）############"
# 说明：v0.36.0 发布于「昨天」，其 pin 的 comfyui-workflow-templates==0.11.62
#       尚未同步到阿里云镜像（镜像最高 0.11.60）。故补一个官方源作为兜底，
#       主源仍是镜像 —— 只有镜像缺失的那一个包会走官方源，代价极小。
PIP_EXTRA="${PIP_EXTRA:---extra-index-url https://pypi.org/simple}"
echo "  额外索引参数: $PIP_EXTRA"
timeout 900 $PIP install --dry-run $PIP_EXTRA --report /root/autodl-tmp/pip_dryrun_report.json -r "$TMPREQ" 2>&1 | tail -45
rc=${PIPESTATUS[0]}
echo "dry-run exit=$rc"

echo
echo "############ 3. 解析结果摘要（将被安装的包）############"
$PY - <<'PY'
import json, os
p = "/root/autodl-tmp/pip_dryrun_report.json"
if not os.path.exists(p):
    print("  无 report 文件（解析未成功）")
    raise SystemExit
d = json.load(open(p))
items = d.get("install", [])
print(f"  将安装/变更 {len(items)} 个包")
for it in items:
    md = it.get("metadata", {})
    print(f"   - {md.get('name'):35s} {md.get('version')}")
PY

echo
echo "############ 4. 关键风险点核查 ############"
$PY - <<'PY'
import json, os
p = "/root/autodl-tmp/pip_dryrun_report.json"
if not os.path.exists(p):
    raise SystemExit
d = json.load(open(p))
names = {it.get("metadata", {}).get("name", "").lower(): it.get("metadata", {}).get("version")
         for it in d.get("install", [])}
risky = ["torch", "torchvision", "numpy", "av", "pillow", "comfy-aimdo", "comfy-kitchen", "comfy-angle"]
for r in risky:
    if r in names:
        print(f"   ⚠ 将变更: {r} -> {names[r]}")
print("   （未列出的包表示不发生变化）")
PY

echo
echo "DRYRUN_DONE"
