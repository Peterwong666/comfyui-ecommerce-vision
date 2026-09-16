#!/usr/bin/env bash
# ============================================================
# 19_probe_torch_path.sh —— 判定「升 torch」还是「降 comfy-kitchen」
#
# 两条路：
#   A. 升级 torch 到 infer_schema 支持 PEP585 的版本（~3GB，牵动 nunchaku）
#   B. 把 comfy-kitchen 降到不用 list[int] 的版本（若存在）
#
# 本脚本只做只读/低代价探测，不改变已装环境。
# ============================================================
set -uo pipefail
cd /root/ComfyUI
PY=/root/miniconda3/bin/python
PIP=/root/miniconda3/bin/pip
export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

echo "############ 1. 磁盘余量（决定 torch 升级可行性）############"
df -h / /root/autodl-tmp | tail -3
echo "torch 现有安装体积："
du -sh /root/miniconda3/lib/python3.12/site-packages/torch 2>/dev/null
du -sh /root/miniconda3/lib/python3.12/site-packages/nvidia 2>/dev/null

echo
echo "############ 2. comfy-kitchen 可用版本 ############"
$PIP index versions comfy-kitchen 2>&1 | head -10

echo
echo "############ 3. 下载若干 comfy-kitchen 版本，检查 conv3d.py 的写法 ############"
TMPD=/root/autodl-tmp/ck_probe
mkdir -p "$TMPD"
for v in 0.1.0 0.2.0 0.2.6 0.2.10 0.2.26 0.2.34; do
  echo "=== comfy-kitchen==$v ==="
  rm -rf "$TMPD/$v"; mkdir -p "$TMPD/$v"
  if timeout 180 $PIP download --no-deps -q -d "$TMPD/$v" "comfy-kitchen==$v" 2>/dev/null; then
    whl=$(ls "$TMPD/$v"/*.whl 2>/dev/null | head -1)
    if [ -n "$whl" ]; then
      # 直接查 wheel 内 conv3d.py 的 stride 注解写法
      line=$(/root/miniconda3/bin/python - "$whl" <<'PY'
import sys, zipfile, re
with zipfile.ZipFile(sys.argv[1]) as z:
    names=[n for n in z.namelist() if n.endswith('backends/eager/conv3d.py')]
    if not names:
        print("  (无 eager/conv3d.py)")
        raise SystemExit
    src=z.read(names[0]).decode('utf-8', 'replace')
    for m in re.finditer(r'stride\s*:\s*([A-Za-z_.\[\]0-9]+)', src):
        print("  stride 注解 =", m.group(1))
    if 'custom_op' in src:
        print("  使用 torch.library.custom_op: 是")
PY
)
      echo "$line"
    else
      echo "  (未取到 wheel)"
    fi
  else
    echo "  (该版本不存在或下载失败)"
  fi
done

echo
echo "############ 4. ComfyUI 官方对 torch 的建议 ############"
grep -rniE 'torch ?[0-9]+\.[0-9]+|torch==|pip install torch' /root/ComfyUI/README.md 2>/dev/null | head -10
echo "--- pyproject 依赖声明 ---"
grep -iE 'torch|dependencies' -A5 /root/ComfyUI/pyproject.toml 2>/dev/null | head -20

echo
echo "############ 5. 当前 driver 支持的 CUDA 上限 ############"
nvidia-smi 2>&1 | head -3 || echo "  (无卡模式，看不到 GPU)"
cat /usr/local/cuda/version.json 2>/dev/null | head -5 || nvcc --version 2>/dev/null | tail -3 || echo "  (未找到 CUDA toolkit 版本信息)"

echo
echo "############ 6. torch 可用版本（cu12x 轮子）############"
timeout 120 $PIP index versions torch 2>&1 | head -5

echo
echo "PROBE_TORCH_DONE"
