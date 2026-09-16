#!/usr/bin/env bash
# ============================================================
# 23_cpu_mode_node_audit.sh —— 用 CPU 模式审计升级后的节点生态（不耗 GPU）
#
# 价值：无卡模式（0.5核/2GB/无GPU）下也能验证
#       「节点能不能加载、工作流要的节点在不在」，
#       把 GPU 机时省给真正的出图验证。
#
# 审计内容：
#   ① ComfyUI 能否在 --cpu 下启动（暴露 import 级错误）
#   ② 节点总数 vs 升级前（v0.3.75 是 2102）
#   ③ 自定义节点加载失败清单（comfyui.log 里的 IMPORT FAILED）
#   ④ flux2 / klein 链路所需节点是否齐备
#   ⑤ 新版的文本编码器路由是否已支持 klein 的 qwen_3_4b
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python
LOG=/root/autodl-tmp/comfyui_cpu_audit.log
PORT=8189

echo "############ 1. 源码层面：新版对 QWEN3_4B 的路由 ############"
grep -n 'TEModel.QWEN3_4B' -A 4 comfy/sd.py | head -20
echo
echo "-- flux2 clip_target 是否已实现（不再是 TODO）--"
grep -n 'class Flux2' -A 30 comfy/supported_models.py | grep -A 6 'def clip_target' | head -10

echo
echo "############ 2. 以 CPU 模式启动 ComfyUI（端口 $PORT）############"
pkill -f "main.py.*--port $PORT" 2>/dev/null
setsid nohup "$PY" main.py --cpu --listen 127.0.0.1 --port $PORT > "$LOG" 2>&1 &
echo "PID=$! 日志=$LOG"

READY=0
for i in $(seq 1 60); do
  if curl -sf --max-time 3 "http://127.0.0.1:$PORT/system_stats" >/dev/null 2>&1; then
    echo "就绪（第 $i 次轮询，约 $((i*5))s）"
    READY=1
    break
  fi
  sleep 5
done

if [ "$READY" -ne 1 ]; then
  echo "!! CPU 模式启动失败/超时，日志尾部："
  tail -40 "$LOG"
  exit 1
fi

echo
echo "############ 3. /system_stats ############"
curl -s "http://127.0.0.1:$PORT/system_stats" | head -c 600
echo

echo
echo "############ 4. 节点总数（升级前 v0.3.75 = 2102）############"
$PY - "$PORT" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/object_info", timeout=180).read())
print(f"  当前节点类总数: {len(d)}")
want = ["CLIPLoader", "CLIPTextEncodeFlux", "UNETLoader", "VAELoader",
        "EmptyFlux2LatentImage", "Flux2Scheduler", "SamplerCustomAdvanced",
        "BasicGuider", "KSamplerSelect", "RandomNoise", "VAEDecode", "SaveImage",
        "CheckpointLoaderSimple", "KSampler", "EmptyLatentImage", "CLIPTextEncode"]
print("\n  -- 关键节点是否齐备 --")
missing = []
for w in want:
    hit = w in d
    if not hit: missing.append(w)
    print(f"   [{'OK' if hit else 'MISSING'}] {w}")
# CLIPLoader 的 clip_name 枚举（确认模型文件被识别）
if "CLIPLoader" in d:
    print("\n  -- CLIPLoader 可选 clip_name --")
    for n in d["CLIPLoader"]["input"]["required"]["clip_name"][0]:
        print("     ", n)
    types = d["CLIPLoader"]["input"]["required"]["type"][0]
    print("  -- CLIPLoader type 是否含 flux2:", "flux2" in types)
print("\n  MISSING_LIST=" + ",".join(missing))
PY

echo
echo "############ 5. 自定义节点加载失败清单 ############"
echo "-- 日志中的导入错误 --"
grep -aiE 'import fail|failed to import|Cannot import|IMPORT FAILED|Traceback' "$LOG" | head -30 || echo "  （无）"
echo
echo "-- custom_nodes 目录现状 --"
ls /root/ComfyUI/custom_nodes | head -45

echo
echo "############ 6. 收尾：停掉 CPU 实例 ############"
pkill -f "main.py.*--port $PORT" 2>/dev/null
sleep 2
echo "已停止"
echo
echo "CPU_AUDIT_DONE"
