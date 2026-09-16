#!/usr/bin/env bash
# ============================================================
# 17_candidate_versions.sh —— 找「支持 klein 且不强制升级 torch」的版本
#
# 背景：v0.36.0 需要比 torch 2.5.1 更新的 torch（comfy_kitchen 的 hip 后端
#       用了 PEP585 的 list[int]，torch 2.5.1 的 infer_schema 不认）。
#       升级 torch = ~3GB 下载 + nunchaku 等自定义节点的连带风险。
#
#       但 FLUX.2 klein 是 2026-01 发布的，那时的 ComfyUI 版本
#       大概率仍与 torch 2.5.1 同期。若能找到这样的版本，
#       就能「拿到 klein 能力」而「不动 torch」，风险小一个数量级。
#
# 判定标准（三项都要满足）：
#   ① 该版本日期 >= 2026-01（klein 发布之后）
#   ② 该版本的 flux2 路由能处理 klein 的文本编码器（qwen_3_4b）
#   ③ requirements.txt 不引入 comfy-kitchen（它才带来 list[int] 问题）
# ============================================================
set -uo pipefail
cd /root/ComfyUI

echo "############ 0. 磁盘余量（torch 升级是否可行）############"
df -h / /root/autodl-tmp | tail -3

echo
echo "############ 1. 候选版本逐个体检 ############"
printf "%-10s %-12s %-14s %-16s %s\n" "TAG" "DATE" "有comfy-kitchen" "flux2-clip_target" "qwen3_4b路由"
printf "%-10s %-12s %-14s %-16s %s\n" "-----" "----" "--------------" "----------------" "-------------"

# 取 2025-11 之后的所有 tag，按日期排序
TAGS=$(git for-each-ref --sort=creatordate --format='%(refname:short)|%(creatordate:short)' refs/tags \
       | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+\|' | awk -F'|' '$2 >= "2025-11-01"')

for row in $TAGS; do
  tag="${row%%|*}"; date="${row##*|}"
  # ② flux2 的 clip_target 是否还是 TODO
  ct=$(git show "$tag":comfy/supported_models.py 2>/dev/null | grep -A3 'class Flux2' | grep -A2 'def clip_target' | grep -o 'return None' | head -1)
  [ -z "$ct" ] && ct="有实现" || ct="TODO(None)"
  # ③ 是否引入 comfy-kitchen
  ck=$(git show "$tag":requirements.txt 2>/dev/null | grep -c 'comfy-kitchen' || true)
  [ "$ck" -gt 0 ] && ck="是" || ck="否"
  # klein 的文本编码器路由：看 QWEN3_4B 分支是否指向 flux
  route=$(git show "$tag":comfy/sd.py 2>/dev/null | grep -A2 'TEModel.QWEN3_4B' | grep -oE 'qwen_image|z_image|flux' | head -1)
  [ -z "$route" ] && route="?"

  printf "%-10s %-12s %-14s %-16s %s\n" "$tag" "$date" "$ck" "$ct" "$route"
done

echo
echo "############ 2. 重点看几个版本的 flux2 路由细节 ############"
for tag in v0.9.2 v0.10.3 v0.11.0 v0.12.0 v0.30.0; do
  echo "=== $tag ($(git log -1 --format=%ad --date=short "$tag" 2>/dev/null)) ==="
  git show "$tag":comfy/sd.py 2>/dev/null | grep -n 'FLUX2\|QWEN3_4B\|MISTRAL3' | head -8 || echo "  (无)"
  echo
done

echo
echo "############ 3. 各候选版本的依赖里有哪些「重」包 ############"
for tag in v0.9.2 v0.11.0 v0.30.0 v0.36.0; do
  echo "--- $tag ---"
  git show "$tag":requirements.txt 2>/dev/null | grep -vE '^\s*#|^\s*$' | head -40 | tr '\n' ' '
  echo
done

echo
echo "CANDIDATES_DONE"
