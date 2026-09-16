#!/usr/bin/env bash
# ============================================================
# 31_restart_prod.sh —— 以生产配置重启 ComfyUI
#
# ⚠️ 为什么这个动作必须放在脚本文件里：
#   直接把 `pkill -f "main.py --listen"` 内联进 ssh 命令会导致**杀掉自己** ——
#   远端执行它的那个 bash -c 的命令行里就包含 "main.py --listen"，
#   pkill -f 会一并匹配到它，于是 shell 在执行到后续启动步骤前就被杀了。
#   （2026-09-16 实际踩到：ComfyUI 被杀掉后没有重新起来，服务空窗。）
#   因此：① 逻辑写成脚本文件执行 ② 停进程用**锚定正则**只匹配真正的 python 进程
# ============================================================
set -uo pipefail

PORT=8188

# 锚定到行首的 python 可执行路径，绝不匹配到 shell 自身
pkill -f '^/root/miniconda3/bin/python main\.py' 2>/dev/null
for _ in $(seq 1 30); do
  curl -sf --max-time 2 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1 || break
  sleep 1
done
sleep 2

echo "=== 以生产配置启动 ==="
bash /root/autodl-tmp/03_start_comfyui.sh 2>&1 | tail -3
sleep 5

echo
echo "=== 生效开关核对 ==="
grep -aiE "Using async weight offloading|DynamicVRAM|Using pytorch attention" \
  /root/autodl-tmp/comfyui.log | head -5
echo "（期望：有 async weight offloading + pytorch attention；**不应**再有 DynamicVRAM enabled）"

echo
echo "=== 健康检查 ==="
curl -s --max-time 8 "http://127.0.0.1:${PORT}/system_stats" \
  | /root/miniconda3/bin/python -c \
    'import json,sys; d=json.load(sys.stdin); print("comfyui_version=" + d["system"]["comfyui_version"]); print("argv=" + " ".join(d["system"]["argv"]))' \
    2>&1 | tail -3

echo
echo "RESTART_DONE"
