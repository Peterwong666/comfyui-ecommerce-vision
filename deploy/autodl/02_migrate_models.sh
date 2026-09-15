#!/usr/bin/env bash
# ============================================================
# 02_migrate_models.sh —— 把 ComfyUI 大目录迁到数据盘并软链（P2-02）
# 背景：autoDL 系统盘仅 30G（剩余约 13G），放不下模型；
#       /root/autodl-tmp 为独立数据盘（50G）。
# 本脚本幂等：重复执行不会破坏已迁移的目录。
# ============================================================
set -Eeuo pipefail

COMFY_DIR="${COMFY_DIR:-/root/ComfyUI}"
DATA_DIR="${DATA_DIR:-/root/autodl-tmp/comfyui-data}"

# 需要迁移的目录（体积会持续增长）
TARGETS=(models output input)

echo "=== 迁移前 ==="
df -h "$COMFY_DIR" "$DATA_DIR" | sed 's/^/  /'

mkdir -p "$DATA_DIR"

for d in "${TARGETS[@]}"; do
  src="$COMFY_DIR/$d"
  dst="$DATA_DIR/$d"

  # 已经是软链则跳过（幂等）
  if [ -L "$src" ]; then
    echo "[skip] $d 已是软链 -> $(readlink "$src")"
    continue
  fi

  if [ -d "$src" ]; then
    if [ -d "$dst" ]; then
      echo "[merge] $d: 目标已存在，合并后删除源目录"
      cp -a "$src/." "$dst/"
      rm -rf "$src"
    else
      echo "[move] $d: $(du -sh "$src" | cut -f1) -> $dst"
      mv "$src" "$dst"
    fi
  else
    echo "[new]  $d: 源不存在，仅创建数据盘目录"
    mkdir -p "$dst"
  fi

  ln -s "$dst" "$src"
  echo "[link] $COMFY_DIR/$d -> $dst"
done

echo
echo "=== 迁移后校验 ==="
for d in "${TARGETS[@]}"; do
  printf "  %-8s %s\n" "$d" "$(ls -ld "$COMFY_DIR/$d" | awk '{print $NF, $(NF-1), $(NF-2)}')"
done

echo
echo "=== 磁盘 ==="
df -h "$DATA_DIR" | sed 's/^/  /'
echo
echo "=== 体积 ==="
du -sh "$DATA_DIR"/* 2>/dev/null | sed 's/^/  /'
