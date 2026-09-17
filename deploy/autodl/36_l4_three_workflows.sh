#!/usr/bin/env bash
# ============================================================
# 36_l4_three_workflows.sh —— P3 三条新工作流的「**渲染路径** L4」一次性验证
#
# 验证对象（三条，均 `render_path_l4: false` / `status: disabled`）：
#   i2i_v1 / inpaint_v1 / upscale_v1
#
# ⚠️ 本脚本验的是**渲染路径** L4，不是「裸提交模板」：
#   走 注册表 param_schema → engine.render() 六步流水线 → POST /prompt。
#   带参考图的三条还必须经 `POST /upload/image` 把素材送进引擎 input/，
#   这正是 A 流生产路径 `_asset_resolver`（backend/app/worker/tasks.py）做的事。
#   **「模板裸提交出过图」推不出「渲染路径能出图」** —— 两者不许混为一谈。
#
# 为什么必须整体上传后执行（runbook §15 红线 3）：
#   内联 ssh 已两次翻车（多层 shell 转义静默产出错误字面量；`pkill -f` 自匹配自杀）。
#
# 幂等性：
#   · ComfyUI 已在跑 → 复用，**不重启**（重启会把已加载的模型赶出显存，白费冷启动）
#   · 素材上传带 overwrite=true → 重复跑不会堆出 `name (1).png` 副本
#   · 证据文件按固定名覆盖 → 重跑不会累积
#
# 退出码：0 = 全部通过（或有跳过但无失败）· 1 = 前置检查未通过 · 2 = 有失败项
#
# 依赖（远端）：
#   $REPO/            engine/ + workflows/ 的副本（由本机 scp 上来）
#   $RD/03_start_comfyui.sh        启动（幂等，默认 --highvram）
#   $RD/37_probe_inpaint_mask_polarity.py   蒙版极性探针
#   $RD/34_bench_recheck.sh        稳态基准复测（**只调用，不重实现**）
#   $REF_IMAGE                     参考图（由本机 scp 上来）
# ============================================================
set -uo pipefail

PY=/root/miniconda3/bin/python
RD=/root/autodl-tmp
REPO="${L4_REPO:-$RD/l4_repo}"
EVID="${L4_EVIDENCE_DIR:-$RD/l4_evidence}"
OUT="${L4_OUTDIR:-$RD/l4_out}"
REF_IMAGE="${L4_REF_IMAGE:-$RD/l4_assets/ref_product.png}"
SEED="${L4_SEED:-20260916}"
COMFY_START_SH="${COMFY_START_SH:-$RD/03_start_comfyui.sh}"
#: 参考图在「素材 id」空间里的编号。**必须同时**用于 `--param reference_image`
#: 与 `--asset`，两处对不上就等于没给素材（见 run_l4 里的说明）。
ASSET_ID=1
PORT=8188
BASE="http://127.0.0.1:$PORT"
T_QUICK=60     # 简单命令（curl / 文件检查）
T_HEAVY=600    # 重命令（出图 / 基准）

export PYTHONPATH="$REPO"   # 让 `-m engine.tools...` 与裸脚本都能 import engine

mkdir -p "$EVID" "$OUT"
STARTED_AT="$(date -Is)"

# 结果汇总（三个数组平行）
NAMES=(); STATUS=(); REASONS=()
record() { NAMES+=("$1"); STATUS+=("$2"); REASONS+=("$3"); }

fail() {
  echo
  echo "❌ 前置检查未通过：$*"
  echo
  echo "PREFLIGHT_FAIL —— **未执行任何出图**，GPU 机时没有被浪费在注定失败的任务上。"
  exit 1
}

report() { echo; echo "==================== $* ===================="; }

# ============================================================ 0. 前置检查
report "0. 前置检查（不通过就立刻退出）"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  fail "nvidia-smi 不存在"
fi
GPU_L="$(nvidia-smi -L 2>&1 | head -3)"
echo "nvidia-smi -L →"
echo "$GPU_L"
# 无卡模式下 nvidia-smi 仍存在，但列不出设备（见 runbook §8 的配合说明）
if ! echo "$GPU_L" | grep -q "^GPU "; then
  fail "看不到任何 GPU 设备（输出见上）—— 实例很可能仍在**无卡模式**。L4 必须有卡。"
fi
if ! "$PY" -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  fail "torch.cuda.is_available() 为 False —— 引擎侧用不上 GPU"
fi
echo "✅ 有卡：$(echo "$GPU_L" | head -1)"
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader

[ -d "$REPO/engine" ] || fail "远端缺 engine 包：$REPO/engine（请先 scp 上传 engine/ workflows/ pyproject.toml）"
[ -f "$REPO/workflows/registry.yaml" ] || fail "远端缺注册表：$REPO/workflows/registry.yaml"
for f in i2i_v1.json inpaint_v1.json upscale_v1.json; do
  [ -f "$REPO/workflows/$f" ] || fail "远端缺工作流定义：$REPO/workflows/$f"
done
echo "✅ engine/ + workflows/ 齐备（$REPO）"

CKPT=/root/autodl-tmp/comfyui-data/models/checkpoints/sd_xl_base_1.0.safetensors
[ -f "$CKPT" ] || fail "缺底模：$CKPT（三条工作流都用它）"
echo "✅ 底模在位：$CKPT"

[ -f "$REF_IMAGE" ] || fail "缺参考图：$REF_IMAGE（i2i/inpaint/upscale 的 reference_image 是必填项）"
echo "✅ 参考图在位：$REF_IMAGE ($(stat -c%s "$REF_IMAGE") bytes)"
echo "   参考图 sha256: $(sha256sum "$REF_IMAGE" | cut -d' ' -f1)"

if curl -sf --max-time 5 "$BASE/system_stats" >/dev/null 2>&1; then
  echo "✅ ComfyUI 已在运行 —— **复用**（不重启，避免丢掉已加载的模型）"
else
  [ -f "$COMFY_START_SH" ] || fail "ComfyUI 未运行，且找不到启动脚本 $COMFY_START_SH"
  echo "ComfyUI 未运行 → 按生产配置启动（$COMFY_START_SH）"
  if ! timeout $T_HEAVY bash "$COMFY_START_SH" > "$EVID/36_comfyui_start.log" 2>&1; then
    tail -20 "$EVID/36_comfyui_start.log"
    fail "启动脚本执行失败（日志见上）"
  fi
  tail -4 "$EVID/36_comfyui_start.log"
  curl -sf --max-time 10 "$BASE/system_stats" >/dev/null 2>&1 \
    || fail "启动脚本返回成功，但 $BASE/system_stats 仍不可达"
  echo "✅ 启动完成"
fi

# 「生效开关」核对（runbook §5.2：这三个开关才是性能的真正决定因素）
{
  echo "# ComfyUI 生效开关（$(date -Is)）"
  echo "# argv: $("$PY" -c "
import json,urllib.request
d=json.load(urllib.request.urlopen('$BASE/system_stats',timeout=10))
print(d['system'].get('argv'))
print('comfyui_version:', d['system'].get('comfyui_version'))
print('devices:', [x.get('name') for x in d.get('devices',[])])
" 2>&1)"
  grep -aE "Set vram state|async weight offload|DynamicVRAM|Using .* attention" "$RD/comfyui.log" 2>/dev/null | tail -6
} > "$EVID/36_comfyui_effective_switches.txt" 2>&1
cat "$EVID/36_comfyui_effective_switches.txt"

# ⚠️ 复现口径硬约束：启动参数必须与生产一致，否则验的不是生产那个配置
if ! grep -qa "Set vram state to: HIGH_VRAM" "$EVID/36_comfyui_effective_switches.txt"; then
  echo "⚠️ 警告：日志里没有 'Set vram state to: HIGH_VRAM' —— 启动参数可能不是生产配置"
  echo "   本脚本**不中止**（L4 验的是渲染路径能否出图，与 VRAM 策略无关），"
  echo "   但性能数字的口径会因此不同，基准复测的结论要打折看。"
fi

# ============================================================ 1. 三条工作流的渲染路径 L4
# 判据（沿用 debug_log §2.4，**不许放宽**）：
#   ① 渲染成功（含 targets 注入落点）② 提交并出图 ③ 同 seed 两次 IDAT 逐字节一致
#   ④ 写元数据未改动像素 ⑤ wf_meta.seed == 传入 seed ⑥ 引擎自写的 prompt chunk 仍在
run_l4() {
  local wf="$1"
  local log="$EVID/36_l4_${wf}.log"
  echo
  echo "-------------------- L4: $wf --------------------"
  # ⚠️ `--param reference_image=` 与 `--asset` 必须**同时**给，且 id 相同。
  #    只给 `--asset 1=…` 是不够的：工具的 params 起点是 Schema 的 default，
  #    而这三条的 `reference_image` 默认值是 **0**（image 类型无法表达"必填"，
  #    见 registry 的契约缺口注释）。于是渲染器会拿 0 去查素材表 →
  #    `RenderError: asset_id=0 未在 --asset 中声明` → 三条全部渲染失败。
  #    这条耦合由 ASSET_ID 单一来源保证；改动时不要只改一处。
  (
    cd "$REPO" && timeout $T_HEAVY "$PY" -m engine.tools.verify_render_path \
      --workflow "$wf" \
      --seed "$SEED" \
      --launch-args="--highvram" \
      --param "reference_image=$ASSET_ID" \
      --asset "$ASSET_ID=${REF_IMAGE}" \
      --outdir "$OUT"
  ) > "$log" 2>&1
  local rc=$?

  # 完整输出留证，终端只给尾部（避免刷屏掩盖汇总）
  grep -E "通过 [0-9]+ · 失败|❌|⏭|fatal" "$log" | sed 's/^/  | /' | head -12

  if [ "$rc" -eq 0 ]; then
    local stat
    stat="$(grep -oE "通过 [0-9]+ · 失败 [0-9]+ · 跳过 [0-9]+" "$log" | tail -1)"
    record "$wf" "通过" "${stat:-通过（未取到计数）}（证据 $log）"
  elif [ "$rc" -eq 1 ]; then
    # ⚠️ 只写 "FAILED" 没有价值 —— 必须带具体失败原因（第一条 ❌ 的内容）
    local why
    why="$(grep -m1 '❌' "$log" | sed 's/^ *//')"
    if [ -z "$why" ]; then
      # 连 ❌ 都没有，说明是崩了（traceback）；抓最后一条异常行
      why="退出码 1，但日志里没有 ❌ 行；异常尾部：$(grep -m1 -E 'Error|Exception|Traceback' "$log" | sed 's/^ *//')"
    fi
    record "$wf" "失败" "$why"
  elif [ "$rc" -eq 124 ]; then
    record "$wf" "失败" "超时：timeout ${T_HEAVY}s 到期（出图未在限时内完成，见 $log）"
  elif [ "$rc" -eq 2 ]; then
    # ⚠️ 退出码 2 有两条来源：脚本自己打的 `[fatal]`，以及 **argparse 的用法错误**
    #    （后者只打 `usage: …` + `error: …`，没有 `fatal`）。
    #    2026-09-17 实测：只 grep 'fatal' ⇒ 原因列为空，汇总表变成「失败：（空）」，
    #    等于没留证据。故按 fatal → error: → 日志末行 逐级回退。
    local why2
    why2="$(grep -m1 'fatal' "$log" | sed 's/^ *//')"
    [ -n "$why2" ] || why2="$(grep -m1 '^.*error: ' "$log" | sed 's/^ *//')"
    [ -n "$why2" ] || why2="$(grep -m1 'usage:' "$log" | sed 's/^ *//')"
    [ -n "$why2" ] || why2="$(tail -1 "$log" | sed 's/^ *//')"
    record "$wf" "失败" "用法/前置错误（退出码 2）：$why2"
  else
    record "$wf" "失败" "异常退出码 $rc（见 $log）"
  fi
}

report "1. 渲染路径 L4：三条工作流逐条跑"
echo "显式 seed = $SEED（确定性验证不能用 -1）· 参考图 asset_id = $ASSET_ID → $REF_IMAGE"
run_l4 i2i_v1
run_l4 inpaint_v1
run_l4 upscale_v1

# ============================================================ 2. inpaint 蒙版极性探针
report "2. inpaint_v1 蒙版极性专项（只给客观信号，语义判断留给人工）"
PROBE_PY="$RD/37_probe_inpaint_mask_polarity.py"
PROBE_JSON="$EVID/36_inpaint_mask_polarity.json"
if [ ! -f "$PROBE_PY" ]; then
  record "inpaint_mask_polarity" "跳过" "探针脚本不存在：$PROBE_PY"
  echo "⏭ 跳过：$PROBE_PY 不存在"
else
  # 先做**只读**源码摘录：这是"机制推断"的依据（明确区别于出图实测）
  SRC="$EVID/36_inpaint_polarity_source.txt"
  {
    echo "# ComfyUI 源码摘录（只读，单变量：不改任何文件）"
    echo "# 采集时间: $(date -Is)"
    echo "# ComfyUI HEAD: $(git -C /root/ComfyUI rev-parse HEAD 2>/dev/null || echo '未知')"
    echo "# 目的：为『蒙版极性』的**机制假设**留下可复核依据。"
    echo "#  ⚠️ 这是源码推断，**不等于**出图实测 —— 语义结论仍需人工看图。"
    echo
    for spec in \
      "/root/ComfyUI/nodes.py:class LoadImage:" \
      "/root/ComfyUI/nodes.py:class ImageCompositeMasked:" \
      "/root/ComfyUI/comfy_extras/nodes_mask.py:class SetLatentNoiseMask:" \
      "/root/ComfyUI/nodes.py:class GrowMask:"
    do
      f="${spec%%:*}"; pat="${spec#*:}"
      echo "=== $pat  （$f） ==="
      if [ -f "$f" ]; then
        grep -n -A42 "$pat" "$f" || echo "（未匹配到 $pat —— 该定义可能已移动，本身就是一条事实）"
      else
        echo "（$f 不存在 —— 该定义可能已移动，本身就是一条事实）"
      fi
      echo
    done
    echo "=== LoadImage 里 mask 由 alpha 导出的那几行（关键）==="
    grep -n -B3 -A3 "mask = 1\. - \|1 - torch.from_numpy(mask)\|1\.-torch" /root/ComfyUI/nodes.py \
      || echo "（未匹配到 '1 - alpha' 形态 —— 需人工读 LoadImage 源码确认）"
  } > "$SRC" 2>&1
  echo "源码摘录 → $SRC"
  grep -c "" "$SRC" | sed 's/^/  行数: /'

  (
    cd "$REPO" && timeout $T_HEAVY "$PY" "$PROBE_PY" \
      --registry "$REPO/workflows/registry.yaml" \
      --base-image "$REF_IMAGE" \
      --seed "$SEED" \
      --base-url "$BASE" \
      --outdir "$OUT/inpaint_polarity" \
      --json "$PROBE_JSON"
  ) > "$EVID/36_inpaint_polarity.log" 2>&1
  rc=$?
  echo "探针输出（尾部）："
  tail -30 "$EVID/36_inpaint_polarity.log" | sed 's/^/  | /'

  if [ "$rc" -eq 0 ]; then
    record "inpaint_mask_polarity" "通过" "客观信号已产出：$PROBE_JSON（**极性结论待人工判读**）"
  elif [ "$rc" -eq 124 ]; then
    record "inpaint_mask_polarity" "失败" "超时：timeout ${T_HEAVY}s 到期（见 $EVID/36_inpaint_polarity.log）"
  else
    record "inpaint_mask_polarity" "失败" "退出码 $rc：$(grep -m1 '❌\|Traceback\|Error' "$EVID/36_inpaint_polarity.log" | sed 's/^ *//')"
  fi
fi

# ============================================================ 3. 稳态基准复测
# ⚠️ **调用**既有脚本，不重实现（runbook §7 的口径：min/median/P95，判读以地板为准）
report "3. 稳态基准复测（调用既有 34_bench_recheck.sh，不重实现）"
if [ ! -f "$RD/34_bench_recheck.sh" ]; then
  record "bench_recheck" "跳过" "缺少 $RD/34_bench_recheck.sh"
  echo "⏭ 跳过：$RD/34_bench_recheck.sh 不存在"
elif [ ! -f "$RD/07_bench_workflow.py" ] \
  || [ ! -f "$RD/t2i_v1.json" ] || [ ! -f "$RD/flux2_klein_t2i_v1.json" ]; then
  record "bench_recheck" "跳过" "34_bench_recheck.sh 的依赖缺失（07_bench_workflow.py / t2i_v1.json / flux2_klein_t2i_v1.json 之一）"
  echo "⏭ 跳过：bench 脚本的依赖不全"
else
  timeout $T_HEAVY bash "$RD/34_bench_recheck.sh" \
    > "$EVID/36_bench_recheck.log" 2>&1
  rc=$?
  grep -aE "min|median|P95|BENCH_DONE|n=" "$EVID/36_bench_recheck.log" | tail -20 | sed 's/^/  | /'
  if [ "$rc" -eq 0 ] && grep -qa "BENCH_DONE" "$EVID/36_bench_recheck.log"; then
    record "bench_recheck" "通过" "原始输出 $EVID/36_bench_recheck.log（判读以 min 地板为准）"
  elif [ "$rc" -eq 124 ]; then
    record "bench_recheck" "失败" "超时：timeout ${T_HEAVY}s 到期（见 $EVID/36_bench_recheck.log）"
  else
    record "bench_recheck" "失败" "退出码 $rc 或无 BENCH_DONE 标记（见 $EVID/36_bench_recheck.log）"
  fi
fi

# ============================================================ 4. 结果汇总表
report "4. 结果汇总"
printf '%-26s %-8s %s\n' "项目" "结论" "依据/原因"
printf '%-26s %-8s %s\n' "--------------------------" "--------" "----------------------------------------"
for i in "${!NAMES[@]}"; do
  printf '%-26s %-8s %s\n' "${NAMES[$i]}" "${STATUS[$i]}" "${REASONS[$i]}"
done
echo
echo "开始: $STARTED_AT"
echo "结束: $(date -Is)  （机时请按这两个时间戳的差值计算）"
echo
echo "⚠️ 本脚本**不**证明画质（属 golden set）与性能达标（属稳态基准 #18）。"
echo "⚠️ 探针的『蒙版极性』只产出**客观信号**；『哪个方向才是对的』必须人工看图确认。"
echo "⚠️ 本脚本**不会**修改 workflows/registry.yaml —— render_path_l4 / status 由人裁定。"

if [ "${#STATUS[@]}" -gt 0 ] && printf '%s\n' "${STATUS[@]}" | grep -q "失败"; then
  echo
  echo "L4_RUN_HAS_FAILURES"
  exit 2
fi
echo
echo "L4_RUN_DONE"
exit 0
