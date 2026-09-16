#!/usr/bin/env bash
# ============================================================
# 29_ab_offload_vram.sh —— 定位 v0.36.0 升级后 SDXL 1.58x 回归的根因
#
# 背景：v0.36.0 相比 v0.3.75 新增两个「Nvidia 上默认开启」的特性：
#   - --async-offload    异步权重卸载（默认 2 条 CUDA 流）
#   - dynamic VRAM       按需流式加载权重（默认开）
# SDXL 仅 6.9GB，24G 显存完全放得下，若仍做流式搬运即为纯开销。
#
# 方法：固定工作流/分辨率/步数，仅切换启动参数，逐个对照。
#   基线（升级前 v0.3.75）: median 4.60s / P95 5.07s
#   现状（v0.36.0 默认）  : median 7.26s / P95 7.72s  ← 待解释
#
# 输出：每个变体的 median / P95，用于判定哪一个开关是元凶
# ============================================================
set -uo pipefail

PY=/root/miniconda3/bin/python
RD=/root/autodl-tmp
PORT=8188
WF=$RD/t2i_v1.json
OUT=$RD/ab_out
N=${N:-8}

mkdir -p "$OUT"

# ---------- 环境指纹（只打一次） ----------
echo "==================== 环境指纹 ===================="
$PY - <<'PYEOF'
import importlib.util, torch
print(f"torch={torch.__version__}")
for m in ("xformers", "flash_attn", "sageattention"):
    s = importlib.util.find_spec(m)
    if s is None:
        print(f"{m}: 未安装")
        continue
    try:
        mod = __import__(m)
        print(f"{m}: {getattr(mod, '__version__', '?')}")
    except Exception as e:
        print(f"{m}: 导入失败 -> {type(e).__name__}: {str(e)[:120]}")
PYEOF

# ---------- 重启 ComfyUI 的工具函数 ----------
stop_comfy() {
  pkill -f "main.py --listen" 2>/dev/null
  for _ in $(seq 1 30); do
    curl -sf --max-time 2 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1 || return 0
    sleep 1
  done
  pkill -9 -f "main.py --listen" 2>/dev/null
  sleep 2
}

start_comfy() {  # 参数: 额外启动参数
  cd /root/ComfyUI
  setsid nohup "$PY" main.py --listen 127.0.0.1 --port "$PORT" "$@" \
    > "$RD/comfyui_ab.log" 2>&1 &
  for _ in $(seq 1 60); do
    if curl -sf --max-time 3 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
      echo "  [启动] 就绪"
      grep -aiE "attention|offload|dynamic" "$RD/comfyui_ab.log" | head -4 | sed 's/^/  [日志] /'
      return 0
    fi
    sleep 3
  done
  echo "  [启动] 超时！日志尾部："
  tail -20 "$RD/comfyui_ab.log"
  return 1
}

# ---------- 逐个变体跑基准 ----------
variant() {  # 参数1=标签，其余=启动参数
  local label="$1"; shift
  echo
  echo "################################################################"
  echo "###  变体: $label"
  echo "###  启动参数: ${*:-（默认，无额外参数）}"
  echo "################################################################"
  stop_comfy
  start_comfy "$@" || return 1
  echo "  -- 基准 N=$N warmup=1 steps=30 --"
  "$PY" "$RD/07_bench_workflow.py" "$WF" -n "$N" --warmup 1 --steps 30 \
      --outdir "$OUT/$label" 2>&1 | tail -14
}

variant v036_default
variant no_async_offload      --disable-async-offload
variant no_offload_no_dynvram --disable-async-offload --disable-dynamic-vram
variant highvram              --highvram

# ---------- 收尾：恢复成默认参数启动 ----------
echo
echo "==================== 恢复默认启动 ===================="
stop_comfy
start_comfy
echo
echo "AB_DONE"
