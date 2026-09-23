#!/usr/bin/env bash
# =============================================================================
# 43_fetch_controlnet_weights.sh —— 取 SDXL ControlNet 权重 + 许可原文证据
#
# 用途：补齐 P4-06 / P4-07 的权重缺口（姿态 / 线稿 / softedge / normal）。
#       默认只下 **xinsir/controlnet-union-sdxl-1.0**（Apache-2.0，一个模型覆盖 10+ 条件）。
#
# ⚠️ 本脚本**纯下载，不需要 GPU** —— 可在 autoDL「无卡模式」跑，省机时。
# ⚠️ 遵守项目纪律「本地写脚本 → scp → 远端执行」；本脚本不做任何删除操作。
# ⚠️ 铁律 1「下载即登记」：脚本会把 README.md（许可声明所在）一并留存为证据，
#    并计算 sha256 —— 拿到输出后必须回写 engine/model_registry.yaml。
#
# 用法（远端）：
#   bash 43_fetch_controlnet_weights.sh                 # 基础版
#   bash 43_fetch_controlnet_weights.sh --with-promax   # 连 ProMax 一起下
#   WITH_DW_POSE_WEIGHTS=1 bash 43_fetch_controlnet_weights.sh   # 顺带去核 DWPose 辅助权重
# =============================================================================
set -euo pipefail

HF_MIRROR="${HF_MIRROR:-https://hf-mirror.com}"
REPO="${REPO:-xinsir/controlnet-union-sdxl-1.0}"
MODELS_DIR="${MODELS_DIR:-/root/autodl-tmp/comfyui-data/models}"
CN_DIR="$MODELS_DIR/controlnet"
EVID_DIR="${EVID_DIR:-/root/autodl-tmp/evidence/43_fetch_cn_weights}"
WITH_PROMAX=0
for a in "$@"; do
  case "$a" in
    --with-promax) WITH_PROMAX=1 ;;
    *) echo "未知参数: $a" >&2; exit 2 ;;
  esac
done

mkdir -p "$CN_DIR" "$EVID_DIR"
echo "== 目标 =================================================="
echo "repo        : $REPO"
echo "mirror      : $HF_MIRROR"
echo "controlnet/ : $CN_DIR"
echo "evidence/   : $EVID_DIR"
echo "promax      : $WITH_PROMAX"
echo

fetch() {  # fetch <相对路径> <输出文件>；成功/失败都打印，失败不中断
  local rel="$1" out="$2"
  local url="$HF_MIRROR/$REPO/resolve/main/$rel"
  echo "-- 下载 $rel"
  if wget -c -q --show-progress -O "$out.part" "$url"; then
    mv "$out.part" "$out"
    echo "   ✅ $(stat -c '%s' "$out") bytes -> $out"
  else
    rm -f "$out.part"
    echo "   ⚠️  失败（可能不存在）：$url"
    return 1
  fi
}

# ---------- 1. 许可证据（README 里的 front-matter 就是许可声明） ----------
echo "== 1. 许可原文证据 =="
fetch "README.md" "$EVID_DIR/README_${REPO//\//_}.md" || true
# 部分仓库另有 LICENSE 文件；没有也不影响（HF 以 front-matter 为准）
fetch "LICENSE" "$EVID_DIR/LICENSE_${REPO//\//_}" || true
echo
echo "---- README front-matter（许可声明，逐字留证）----"
head -20 "$EVID_DIR/README_${REPO//\//_}.md" 2>/dev/null || echo "（README 未取到）"
echo "-----------------------------------------------------"
echo

# ---------- 2. 权重本体 ----------
echo "== 2. 权重 =="
fetch "diffusion_pytorch_model.safetensors" "$CN_DIR/controlnet-union-sdxl-1.0.safetensors" || true
if [ "$WITH_PROMAX" = "1" ]; then
  fetch "diffusion_pytorch_model_promax.safetensors" "$CN_DIR/controlnet-union-sdxl-1.0-promax.safetensors" || true
fi
echo

# ---------- 3. 登记所需的事实（sha256 / size） ----------
echo "== 3. sha256 与体积（回写 model_registry.yaml 用）=="
for f in "$CN_DIR"/controlnet-union-sdxl-1.0*.safetensors; do
  [ -e "$f" ] || continue
  printf '%s  %s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$(stat -c '%s' "$f")" "$(basename "$f")"
done
echo

# ---------- 4. 顺带清点：已在用但未登记的权重 + 预处理器辅助权重 ----------
echo "== 4. 清点现场（用于补登记 / 查红线）=="
echo "---- controlnet/ 现有文件 ----"
ls -la "$CN_DIR" 2>/dev/null || echo "（空）"
echo
echo "---- comfyui_controlnet_aux 的辅助权重 ckpts/ ----"
AUX_CKPTS="$(find /root/ComfyUI/custom_nodes -maxdepth 3 -type d -name ckpts 2>/dev/null | head -5)"
if [ -n "$AUX_CKPTS" ]; then
  while IFS= read -r d; do
    echo "[$d]"
    find "$d" -type f \( -name '*.pth' -o -name '*.onnx' -o -name '*.pt' \) -printf '%s\t%p\n' 2>/dev/null | sort -k2
  done <<< "$AUX_CKPTS"
  echo
  echo "⚠️ 其中 body_pose_model.pth = CMU OpenPose 权重（**非商用**），见 license_matrix.md §3.1"
  echo "   姿态控制须改用 DWPreprocessor；本清单用于按 §8 登记流程逐条补登记。"
else
  echo "（未找到 ckpts/ 目录）"
fi
echo
if [ "${WITH_DW_POSE_WEIGHTS:-0}" = "1" ]; then
  echo "---- DWPose 辅助权重（yolox_l.onnx / dw-ll_ucoco_384.onnx）来源核验 ----"
  find / -name 'dw-ll_ucoco_384.onnx' -o -name 'yolox_l.onnx' 2>/dev/null | head -5
fi

# ---------- 5. 机读摘要 ----------
echo "== 5. JSON 摘要 =="
{
  echo "{"
  echo "  \"fetched_at\": \"$(date -Iseconds)\","
  echo "  \"repo\": \"$REPO\","
  echo "  \"mirror\": \"$HF_MIRROR\","
  echo "  \"files\": ["
  first=1
  for f in "$CN_DIR"/controlnet-union-sdxl-1.0*.safetensors; do
    [ -e "$f" ] || continue
    [ "$first" = "1" ] || echo ","
    first=0
    printf '    {"name": "%s", "size": %s, "sha256": "%s"}' \
      "$(basename "$f")" "$(stat -c '%s' "$f")" "$(sha256sum "$f" | cut -d' ' -f1)"
  done
  echo
  echo "  ]"
  echo "}"
} | tee "$EVID_DIR/fetched.json"

echo
echo "✅ 完成。回本机后请："
echo "   ① 用上面第 3 节的 sha256/size 回写 engine/model_registry.yaml（planned → models）"
echo "   ② 把 README 的 front-matter 原文记进 docs/sop/license_matrix.md §9"
echo "   ③ 用第 4 节的清单补登记已在用但未登记的权重（§7 #17）"
