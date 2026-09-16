#!/usr/bin/env bash
# ============================================================
# 32_collect_offline_evidence.sh —— 一次性采集「离线校验」所需的全部服务器证据
#
# 背景：两处阻塞都指向同一批原始数据 ——
#   B 流：L3 节点合法性校验需要 object_info 缓存（26_validate_workflow.py --object-info）
#   C 流：节点白名单的 status 判定需要 import 级故障清单 + 节点 commit
#
# 为什么能用无卡模式跑：
#   `--cpu` 模式不加载任何模型、不碰 GPU，只是把节点类注册表建出来并吐 /object_info。
#   今天上午的 23_cpu_mode_node_audit.sh 已验证过这条路可行。
#
# ⚠️ 停进程用**锚定正则**（见 项目进展.md #19）：
#   不锚定会在 shell 命令行里自匹配，把执行它的父 shell 一起杀掉。
# ============================================================
set -uo pipefail

PY=/root/miniconda3/bin/python
COMFY=/root/ComfyUI
RD=/root/autodl-tmp
OUT=$RD/offline_evidence
PORT=8199                      # 错开 8188，避免撞上可能残留的实例
VER=$("$PY" -c 'import json,sys;print("v0.36.0")' 2>/dev/null || echo v0.36.0)

mkdir -p "$OUT"

echo "==================== 0. 环境指纹 ===================="
echo -n "ComfyUI: "; git -C "$COMFY" describe --tags 2>/dev/null
echo -n "commit:  "; git -C "$COMFY" rev-parse HEAD 2>/dev/null
echo -n "torch:   "; "$PY" -c 'import torch;print(torch.__version__)' 2>&1 | tail -1
echo -n "内存上限: "; cat /sys/fs/cgroup/memory.max 2>/dev/null
echo -n "CPU 配额: "; cat /sys/fs/cgroup/cpu.max 2>/dev/null
echo -n "GPU:     "; nvidia-smi -L 2>&1 | head -1

echo
echo "==================== 1. 自定义节点清单（含 git commit） ===================="
{
  echo "# 采集时间: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "# 路径: $COMFY/custom_nodes/"
  cd "$COMFY/custom_nodes" || exit 1
  for d in */; do
    n="${d%/}"
    [ "$n" = "__pycache__" ] && continue
    if [ -d "$d/.git" ]; then
      c=$(git -C "$d" rev-parse HEAD 2>/dev/null || echo ERR)
      s=$(git -C "$d" status --porcelain 2>/dev/null | wc -l)
      echo "$n commit=$c dirty_files=$s"
    else
      echo "$n NO_GIT"
    fi
  done
} > "$OUT/custom_nodes_inventory.txt" 2>&1
echo "  -> $(wc -l < "$OUT/custom_nodes_inventory.txt") 行"
echo "  节点数（不含注释）: $(grep -vc '^#' "$OUT/custom_nodes_inventory.txt")"

echo
echo "==================== 2. 启动 ComfyUI（--cpu，不吃 GPU） ===================="
# 先确保没有残留（锚定到 python 可执行文件路径，绝不匹配 shell 自身）
pkill -f '^/root/miniconda3/bin/python main\.py' 2>/dev/null
sleep 2

cd "$COMFY" || exit 1
setsid nohup "$PY" main.py --cpu --listen 127.0.0.1 --port "$PORT" \
  > "$OUT/comfyui_cpu_startup.log" 2>&1 < /dev/null &

READY=0
for i in $(seq 1 100); do
  if curl -sf --max-time 3 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
    READY=1; echo "  就绪，耗时约 $((i*3))s（CPU 模式 + 0.5 核，慢是正常的）"; break
  fi
  sleep 3
done

if [ "$READY" != "1" ]; then
  echo "  ❌ 启动超时。日志尾部："
  tail -30 "$OUT/comfyui_cpu_startup.log"
else
  echo
  echo "==================== 3. 导出 object_info ===================="
  curl -s --max-time 120 "http://127.0.0.1:${PORT}/object_info" > "$OUT/object_info.${VER}.json"
  SZ=$(stat -c '%s' "$OUT/object_info.${VER}.json" 2>/dev/null || echo 0)
  echo "  -> $OUT/object_info.${VER}.json  ($SZ 字节)"
  if [ "$SZ" -gt 100000 ]; then
    "$PY" - "$OUT/object_info.${VER}.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"  节点类总数: {len(d)}")
PYEOF
  else
    echo "  ⚠️ 体积异常偏小，可能没抓到，请检查日志"
  fi
fi

echo
echo "==================== 4. 节点 import 故障清单 ===================="
grep -aiE "Cannot import|IMPORT FAILED|Traceback|Error|缺失|失败" "$OUT/comfyui_cpu_startup.log" \
  | head -60 > "$OUT/node_import_failures.txt" 2>&1
echo "  -> $(wc -l < "$OUT/node_import_failures.txt") 行候选（需人工判读）"

echo
echo "==================== 5. 收尾：停掉 CPU 实例 ===================="
pkill -f '^/root/miniconda3/bin/python main\.py' 2>/dev/null
sleep 2
if pgrep -f '^/root/miniconda3/bin/python main\.py' >/dev/null 2>&1; then
  echo "  ⚠️ 仍有残留进程"; pgrep -af '^/root/miniconda3/bin/python main\.py'
else
  echo "  ✅ 已停止"
fi

echo
echo "==================== 产物清单 ===================="
ls -la "$OUT/"
echo
echo "COLLECT_DONE"
