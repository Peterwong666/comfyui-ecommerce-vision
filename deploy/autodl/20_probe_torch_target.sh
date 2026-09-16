#!/usr/bin/env bash
# ============================================================
# 20_probe_torch_target.sh —— 选定 torch 目标版本并评估体积/磁盘
#
# 约束（来自 ComfyUI v0.36.0 README）：
#   - 最低 torch 2.7
#   - 20 系及以上必须 cu130+
#   - 「推荐最新大版本，除非它发布不足 2 周」
#
# 额外约束（本项目环境）：
#   - 系统盘 overlay 仅剩约 14G
#   - 驱动 580.76.05（CUDA 13 分支）
# ============================================================
set -uo pipefail
PY=/root/miniconda3/bin/python

echo "############ 1. torch 各版本发布日期（判断是否 <2 周）############"
$PY - <<'PY'
import json, urllib.request, datetime
url = "https://pypi.org/pypi/torch/json"
try:
    d = json.loads(urllib.request.urlopen(url, timeout=40).read())
    rels = d["releases"]
    want = ["2.14.0", "2.13.0", "2.12.1", "2.12.0", "2.11.0", "2.10.0", "2.9.1", "2.7.1"]
    today = datetime.date(2026, 9, 16)
    for v in want:
        if v in rels and rels[v]:
            dt = min(f["upload_time"][:10] for f in rels[v])
            days = (today - datetime.date.fromisoformat(dt)).days
            flag = "  <-- 不足2周，按官方建议应回避" if days < 14 else ""
            print(f"  torch {v:8s} 发布 {dt}  距今 {days:4d} 天{flag}")
        else:
            print(f"  torch {v:8s} (未找到)")
except Exception as e:
    print("  查询 pypi.org 失败:", e)
PY

echo
echo "############ 2. cu130 轮子是否存在（用 pip index 探测下载源）############"
echo "-- download.pytorch.org 可达性 --"
for u in https://download.pytorch.org/whl/cu130/ https://download.pytorch.org/whl/cu128/; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$u" 2>/dev/null)
  echo "  [$code] $u"
done

echo
echo "############ 3. 当前 torch/torchvision/torchaudio 体积与磁盘 ############"
du -sh /root/miniconda3/lib/python3.12/site-packages/torch \
       /root/miniconda3/lib/python3.12/site-packages/torchvision \
       /root/miniconda3/lib/python3.12/site-packages/torchaudio 2>/dev/null
echo "-- nvidia-* 子包 --"
ls -d /root/miniconda3/lib/python3.12/site-packages/nvidia* 2>/dev/null | head -20
du -sh /root/miniconda3/lib/python3.12/site-packages/nvidia 2>/dev/null
echo
df -h / /root/autodl-tmp | tail -3

echo
echo "############ 4. 数据盘是否已存在可用的 conda 环境 ############"
ls -d /root/autodl-tmp/*env* /root/autodl-tmp/conda* 2>/dev/null || echo "  (无)"
/root/miniconda3/bin/conda env list 2>/dev/null | head -10

echo
echo "############ 5. 自定义节点中依赖 torch 二进制/编译扩展的（升级受影响面）############"
for d in /root/ComfyUI/custom_nodes/*/; do
  name=$(basename "$d")
  [ -d "$d/.git" ] || continue
  # 找 .so / .pyd 或 requirements 里提到 torch 的
  so=$(find "$d" -maxdepth 3 \( -name '*.so' -o -name '*.pyd' \) 2>/dev/null | head -1)
  treq=$(grep -rl 'torch' "$d"/requirements.txt "$d"/pyproject.toml 2>/dev/null | head -1)
  if [ -n "$so" ] || [ -n "$treq" ]; then
    echo "  $name  $([ -n "$so" ] && echo '[含编译扩展]')$([ -n "$treq" ] && echo '[声明torch依赖]')"
  fi
done

echo
echo "PROBE_TORCH_TARGET_DONE"
