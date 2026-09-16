#!/usr/bin/env bash
# ============================================================
# 12_upgrade_checkout.sh —— 把 ComfyUI 切到目标版本（只切代码，不装依赖）
#
# ⚠️ 本脚本处理一个真实隐患：
#   models/input/output 已迁至数据盘并软链回原位，git 视其内文件为"已删除"。
#   若直接 git checkout -f，git 会重建**真实目录**，把软链顶掉，
#   导致 ComfyUI 再也看不到数据盘上的模型（表现为"模型全没了"）。
#   故流程为：摘链 → checkout → 删掉 git 新建的空目录 → 挂回软链。
#
# 安全设计：
#   - 删除前校验「是真实目录、非软链、体积 < 10MB」，否则中止（防误删真数据）
#   - checkout 前打印回退命令，任何时候都能一条命令退回
#   - 不执行 pip install（拆到 13 脚本，便于中间检查）
# ============================================================
set -uo pipefail
COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
TARGET="${TARGET:-v0.36.0}"
BACKUP_DIR=/root/link_backup
cd "$COMFY_DIR"

echo "############ 0. 前置检查 ############"
git rev-parse "$TARGET" >/dev/null 2>&1 || { echo "!! $TARGET 不存在，先跑 11_fetch_target.sh"; exit 1; }
if pgrep -f "git fetch" >/dev/null 2>&1; then
  echo "!! 仍有 git fetch 在运行，请等待其结束"; exit 1
fi
FROM_COMMIT=$(git rev-parse HEAD)
echo "FROM = $(git describe --tags 2>/dev/null || echo $FROM_COMMIT) ($FROM_COMMIT)"
echo "TO   = $TARGET ($(git rev-parse "$TARGET"))"
echo
echo "▶ 回退命令（任何时候可用）："
echo "   cd $COMFY_DIR && git checkout -f v0.3.75"
echo

echo "############ 1. 记录并摘下软链 ############"
mkdir -p "$BACKUP_DIR"
LINKED=()
for d in models input output; do
  if [ -L "$d" ]; then
    tgt=$(readlink "$d")
    echo "  $d -> $tgt"
    mv "$d" "$BACKUP_DIR/$d.link"
    LINKED+=("$d")
  else
    echo "  $d 不是软链，跳过（可能是真实目录）"
  fi
done
echo "已摘链: ${LINKED[*]:-无}"

echo
echo "############ 2. 清理已知脏目录 ############"
if [ -d 'custom_nodes/$LOCK' ]; then
  echo " 发现 custom_nodes/\$LOCK（09-15 shell 转义事故残留），内容："
  ls -la 'custom_nodes/$LOCK' | head -5
  rm -rf 'custom_nodes/$LOCK'
  echo " 已删除"
else
  echo " 无 \$LOCK 残留"
fi

echo
echo "############ 3. 切换到 $TARGET ############"
git checkout -f "$TARGET" 2>&1 | tail -10
rc=$?
echo "checkout rc=$rc"
if [ "$rc" -ne 0 ]; then
  echo "!! checkout 失败，立即回滚软链"
  for d in "${LINKED[@]}"; do
    [ -e "$d" ] && rm -rf "$d"
    mv "$BACKUP_DIR/$d.link" "$d"
  done
  exit 1
fi
echo "现在 HEAD: $(git log -1 --format='%h %ad %s' --date=short)"

echo
echo "############ 4. 挂回软链 ############"
for d in "${LINKED[@]}"; do
  if [ -e "$d" ] || [ -L "$d" ]; then
    if [ -L "$d" ]; then
      echo "  $d 已是软链，跳过"
      continue
    fi
    # 安全护栏：确认是本次 git 新建的空占位目录，才允许删除
    if [ -d "$d" ]; then
      size_kb=$(du -sk "$d" | cut -f1)
      echo "  git 新建了真实目录 $d（${size_kb}KB），文件数 $(find "$d" -type f | wc -l)"
      if [ "$size_kb" -lt 10240 ]; then
        rm -rf "$d"
        echo "  已删除（<10MB，判定为占位目录）"
      else
        echo "  !! $d 体积 ${size_kb}KB 超过 10MB 安全阈值，**拒绝删除**，请人工检查！"
        exit 2
      fi
    fi
  fi
  mv "$BACKUP_DIR/$d.link" "$d"
  echo "  已挂回 $d -> $(readlink "$d")"
done

echo
echo "############ 5. 校验软链与模型可见性 ############"
for d in models input output; do
  [ -L "$d" ] && echo "  [软链OK] $d -> $(readlink "$d")" || echo "  [警告] $d 不是软链"
done
echo "-- 关键模型文件是否可见 --"
for f in models/checkpoints/sd_xl_base_1.0.safetensors \
         models/unet/flux-2-klein-4b.safetensors \
         models/vae/flux2-vae.safetensors \
         models/text_encoders/qwen_3_4b.safetensors; do
  [ -f "$f" ] && echo "  [OK] $f ($(du -h "$f" | cut -f1))" || echo "  [缺失] $f"
done

echo
echo "############ 6. 依赖差异（先看，不装）############"
diff <(git show v0.3.75:requirements.txt) <(cat requirements.txt) || true
echo
echo "CHECKOUT_DONE"
