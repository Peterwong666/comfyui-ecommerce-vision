#!/usr/bin/env bash
# ============================================================
# 25_verify_klein_route.sh —— 确认 v0.36.0 把 klein 的 TE 路由到 flux2
#
# 这是整轮升级的「关键假设」：v0.3.75 里 QWEN3_4B 被路由去 z_image，
# 导致 klein 跑不起来。若 v0.36.0 仍是如此，那升级也没解决问题。
#
# 只读源码 + 一次 CPU 模式启停，不耗 GPU。
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python
LOG=/root/autodl-tmp/comfyui_cpu_audit.log
PORT=8189

echo "############ 1. 源码：QWEN3_4B 现在被路由到哪里 ############"
grep -n 'TEModel.QWEN3_4B' -A 6 comfy/sd.py | head -25

echo
echo "############ 2. 源码：detect_te_model 怎么识别 klein 的 TE ############"
grep -n 'def detect_te_model' -A 60 comfy/sd.py | grep -nE 'QWEN|MISTRAL|FLUX2|hidden|k_proj|q_proj|return' | head -25

echo
echo "############ 3. 源码：Flux2.clip_target 是否已实现 ############"
grep -n 'class Flux2' -A 35 comfy/supported_models.py | grep -A 8 'def clip_target' | head -12

echo
echo "############ 4. 源码：是否存在 klein 专用节点/类 ############"
grep -rniE 'klein' comfy/ comfy_extras/ 2>/dev/null | grep -v '.pyc' | head -10 || echo "  (无 klein 字样，说明按架构而非品牌识别)"

echo
echo "############ 5. CPU 模式实测：节点总数与加载失败清单 ############"
pkill -f "main.py.*--port $PORT" 2>/dev/null
setsid nohup "$PY" main.py --cpu --listen 127.0.0.1 --port $PORT > "$LOG" 2>&1 &
for i in $(seq 1 60); do
  curl -sf --max-time 3 "http://127.0.0.1:$PORT/system_stats" >/dev/null 2>&1 && break
  sleep 5
done

$PY - "$PORT" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/object_info", timeout=180).read())
print(f"  节点类总数: {len(d)}   （升级前 v0.3.75 = 2102）")
# 与 flux2 文本编码相关的节点
print("\n  -- 含 Flux2 / CLIPTextEncode 的节点 --")
for k in sorted(d):
    if "flux2" in k.lower() or "CLIPTextEncodeFlux" == k:
        print("    ", k)
# CLIPLoader 的 type 枚举与 clip_name
cl = d.get("CLIPLoader", {})
if cl:
    req = cl["input"]["required"]
    print("\n  -- CLIPLoader.type 枚举 --")
    print("    ", req["type"][0])
PY

echo
echo "############ 6. 加载失败清单（IMPORT FAILED）############"
grep -aE 'IMPORT FAILED' "$LOG" | head -20 || echo "  （无）"
echo "-- 全部 ERROR 行 --"
grep -aE '^\[ERROR\]' "$LOG" | head -20 || echo "  （无）"

pkill -f "main.py.*--port $PORT" 2>/dev/null
sleep 2
echo
echo "VERIFY_KLEIN_ROUTE_DONE"
