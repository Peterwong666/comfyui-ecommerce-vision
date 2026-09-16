#!/usr/bin/env bash
# ============================================================
# 35_fix_sageattention.sh —— 卸载 sageattention，复测被它毒化的节点
#
# 背景（项目进展.md #23）：
#   升级后 3 个自定义节点失效。当时的归因是 nunchaku=torch ABI /
#   TeaCache=precompute_freqs_cis 被移除 / smZNodes=diffusers。
#   C 流读审计日志原文后发现：**smZNodes 的真因不是 diffusers，而是
#   `sageattention==2.2.0` 的 ABI 失配** —— 它损坏的 .so 通过
#   `diffusers/attention_dispatch.py:72` 的**模块级 import** 毒化整条
#   diffusers 链，而 nunchaku 的首层报错与它同源。
#
#   ⚠️ 关键点：该包被 ComfyUI **"回退使用"**（检测到导入失败就降级），
#   因此**从未被卸载**，versions.lock 的 [pip] 段里至今仍列着它。
#   这与 #16 处置 10 卸载 flash-attn 是**同一类问题**，当时只修了一半。
#
# 判据（与 #16 一致）：它是**可选加速**，缺失时 ComfyUI 自动回退原生注意力。
#
# ⚠️ 停进程一律用**锚定正则**（#19：pkill -f 模式串若出现在执行它的
#    shell 命令行里会自杀）。本脚本整体上传执行，父进程命令行不含模式串。
# ============================================================
set -uo pipefail

PY=/root/miniconda3/bin/python
COMFY=/root/ComfyUI
RD=/root/autodl-tmp
OUT=$RD/sageattention_fix
PORT=8199          # 错开 8188，避免撞上 GPU 模式残留实例

mkdir -p "$OUT"

echo "==================== 0. 现状留证 ===================="
"$PY" -m pip show sageattention 2>&1 | sed -n '1,6p' | tee "$OUT/before_pip_show.txt"
echo
echo "-- 相关 .egg / .so 文件 --"
ls -la /root/miniconda3/lib/python3.12/site-packages/ 2>/dev/null | grep -i sage | tee -a "$OUT/before_pip_show.txt"
echo
echo "-- 它是否真的坏（导入测试）--"
"$PY" -c "import sageattention" 2>&1 | tail -3 | tee "$OUT/before_import.txt"

echo
echo "==================== 1. 卸载 sageattention ===================="
"$PY" -m pip uninstall -y sageattention 2>&1 | tail -6
echo
echo "-- 卸载后仍能从 diffusers 链导入吗（关键判据）--"
"$PY" -c "
try:
    import diffusers
    print(f'  ✅ diffusers 导入成功 {diffusers.__version__}')
    from diffusers.models.autoencoders.autoencoder_kl import AutoencoderKL
    print('  ✅ AutoencoderKL 可导入（毒化链已解开）')
except Exception as e:
    print(f'  ❌ 仍失败：{type(e).__name__}: {str(e)[:200]}')
" 2>&1 | tail -5

echo
echo "==================== 2. 卸载残留检查 ===================="
ls -la /root/miniconda3/lib/python3.12/site-packages/ 2>/dev/null | grep -i sage || echo "  ✅ 无 sage 残留"

echo
echo "==================== 3. CPU 模式启动 ComfyUI 复测节点 ===================="
pkill -f '^/root/miniconda3/bin/python main\.py' 2>/dev/null
sleep 2
cd "$COMFY" || exit 1
setsid nohup "$PY" main.py --cpu --listen 127.0.0.1 --port "$PORT" \
  > "$OUT/comfyui_cpu_after.log" 2>&1 < /dev/null &

READY=0
for i in $(seq 1 100); do
  if curl -sf --max-time 3 "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
    READY=1; echo "  就绪（约 $((i*3))s）"; break
  fi
  sleep 3
done

if [ "$READY" != "1" ]; then
  echo "  ❌ 启动超时，日志尾部："; tail -25 "$OUT/comfyui_cpu_after.log"
else
  echo
  echo "-- 节点类总数（对比基线 2530）--"
  curl -s --max-time 120 "http://127.0.0.1:${PORT}/object_info" > "$OUT/object_info_after.json"
  "$PY" - "$OUT/object_info_after.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"  当前节点类总数: {len(d)}  （升级后基线 2530）")
for k in ("smZNodes", "BNK_CLIPTextEncodeAdvanced", "nunchaku"):
    hits = [n for n in d if k.lower() in n.lower()]
    print(f"  含 '{k}' 的节点类: {len(hits)}")
PYEOF
fi

echo
echo "-- 三个目标节点是否仍然 IMPORT FAILED（关键判据）--"
grep -aE "IMPORT FAILED|Cannot import" "$OUT/comfyui_cpu_after.log" \
  | grep -aiE "smZNodes|nunchaku|TeaCache" | tee "$OUT/import_failed_after.txt" || echo "  ✅ 三者均未出现在 IMPORT FAILED 列表中"

echo
echo "-- 与升级当天审计日志对照（那些节点当时是否失败）--"
for n in smZNodes nunchaku TeaCache; do
  before=$(grep -ac "IMPORT FAILED.*$n\|$n.*IMPORT FAILED" "$RD/comfyui_cpu_audit.log" 2>/dev/null || echo 0)
  after=$(grep -ac "IMPORT FAILED.*$n\|$n.*IMPORT FAILED" "$OUT/comfyui_cpu_after.log" 2>/dev/null || echo 0)
  echo "  $n: 升级当天失败行=$before · 现在失败行=$after"
done

echo
echo "==================== 4. 收尾 ===================="
pkill -f '^/root/miniconda3/bin/python main\.py' 2>/dev/null
sleep 2
if pgrep -f '^/root/miniconda3/bin/python main\.py' >/dev/null 2>&1; then
  echo "  ⚠️ 仍有残留"; pgrep -af '^/root/miniconda3/bin/python main\.py'
else
  echo "  ✅ CPU 实例已停止"
fi

echo
echo "-- 卸载后 pip 状态（versions.lock 的 [pip] 段需据此重生成）--"
"$PY" -m pip freeze 2>/dev/null | grep -i sage || echo "  ✅ pip freeze 中已无 sageattention"

echo
echo "产物：$OUT/"
ls -la "$OUT/"
echo
echo SAGE_DONE
