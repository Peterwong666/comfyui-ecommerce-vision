#!/usr/bin/env bash
# ============================================================
# 21_dryrun_torch.sh —— 预演 torch 升级（只解析，不落地）
#
# 目标：torch 2.13.0 + cu130（依据 ComfyUI v0.36.0 README：
#       20 系以上必须 cu130+；推荐最新大版本但回避 <2 周的 2.14.0）
#
# 要回答：torchvision / torchaudio 会被解析成哪个版本？
#         下载量多大？系统盘 14G 够不够？
# ============================================================
set -uo pipefail
PY=/root/miniconda3/bin/python
PIP=/root/miniconda3/bin/pip
export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

IDX="https://download.pytorch.org/whl/cu130"

echo "############ 0. 现况 ############"
df -h / | tail -1
$PIP list 2>/dev/null | grep -iE '^(torch|torchvision|torchaudio) '

echo
echo "############ 1. dry-run 解析（torch 固定 2.13.0，另两个交给解析器配套）############"
timeout 900 $PIP install --dry-run --report /root/autodl-tmp/torch_dryrun.json \
  --index-url "$IDX" \
  "torch==2.13.0" torchvision torchaudio 2>&1 | tail -25
echo "exit=${PIPESTATUS[0]}"

echo
echo "############ 2. 解析结果：将被安装/变更的包与体积 ############"
$PY - <<'PY'
import json, os
p = "/root/autodl-tmp/torch_dryrun.json"
if not os.path.exists(p):
    print("  无 report（解析失败）"); raise SystemExit
d = json.load(open(p))
arch = d.get("environment", {}).get("markers", {})
tot = 0
print(f"{'包名':32s} {'版本':16s} {'大小(MB)':>10s}")
for it in d.get("install", []):
    md = it.get("metadata", {})
    dl = it.get("download_info", {}) or {}
    sz = it.get("archive_info", {}).get("size") or 0
    # pip report 里体积字段名随版本不同，兜底从 url 猜
    tot += sz
    print(f"{md.get('name',''):32s} {str(md.get('version','')):16s} {sz/1048576:10.1f}")
print(f"\n合计(已标体积的包): {tot/1048576/1024:.2f} GB")
print(f"包数: {len(d.get('install', []))}")
PY

echo
echo "############ 3. nvidia cu13 子包体积（主要占用来源）############"
$PY - <<'PY'
import json, os
p = "/root/autodl-tmp/torch_dryrun.json"
if not os.path.exists(p): raise SystemExit
d = json.load(open(p))
for it in d.get("install", []):
    md = it.get("metadata", {})
    n = md.get("name","")
    if n.lower().startswith("nvidia") or n in ("torch","torchvision","torchaudio"):
        print(f"   {n:32s} {md.get('version')}")
PY

echo
echo "############ 4. 下载量预估（真实测量：各 wheel 的 Content-Length 之和）############"
$PY - <<'PY'
import json, os, urllib.request
p = "/root/autodl-tmp/torch_dryrun.json"
if not os.path.exists(p): raise SystemExit
d = json.load(open(p))
total = 0
for it in d.get("install", []):
    di = it.get("download_info", {}) or {}
    url = di.get("url")
    if not url: continue
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            n = int(r.headers.get("Content-Length", 0))
            total += n
    except Exception as e:
        print("   HEAD 失败:", url.split('/')[-1], e)
print(f"   预计下载总量: {total/1073741824:.2f} GB")
PY

echo
echo "TORCH_DRYRUN_DONE"
