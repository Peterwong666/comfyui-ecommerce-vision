#!/usr/bin/env bash
# ============================================================
# 15_probe_pypi.sh —— 探测包源可达性与备选版本的依赖可用性
#
# 背景：v0.36.0 是「昨天」发布的，其 pin 的 comfyui-workflow-templates==0.11.62
#       在阿里云镜像上尚未同步（镜像最高 0.11.60）。
#       这属于典型的「追最新版 = 追镜像同步进度」问题。
#
# 本脚本回答三个问题：
#   ① pypi.org 官方源可达吗？
#   ② 清华镜像可达吗？
#   ③ 退一档到 v0.35.2，其依赖是否已在镜像上齐备？
# ============================================================
set -uo pipefail
cd /root/ComfyUI

echo "############ 1. 各包源可达性 ############"
for u in https://pypi.org/simple/ https://pypi.tuna.tsinghua.edu.cn/simple/ http://mirrors.aliyun.com/pypi/simple/; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$u" 2>/dev/null)
  t=$(curl -s -o /dev/null -w '%{time_total}s' --max-time 20 "$u" 2>/dev/null)
  echo "  [$code] $t  $u"
done

echo
echo "############ 2. 当前 pip 源配置 ############"
/root/miniconda3/bin/pip config list 2>&1
cat /root/.pip/pip.conf 2>/dev/null || cat /etc/pip.conf 2>/dev/null || echo "  (无配置文件)"

echo
echo "############ 3. 官方源能否查到缺失的那个包 ############"
curl -s --max-time 25 https://pypi.org/pypi/comfyui-workflow-templates/json 2>/dev/null \
  | /root/miniconda3/bin/python -c "
import json,sys
try:
    d=json.load(sys.stdin)
    vs=sorted(d['releases'].keys())
    print('  pypi.org 上共有', len(vs), '个版本，最新 5 个:', vs[-5:])
    print('  是否含 0.11.62:', '0.11.62' in vs)
except Exception as e:
    print('  查询失败:', e)
" 2>&1

echo
echo "############ 4. 备选版本 v0.35.2 的依赖 ############"
for tag in v0.36.0 v0.35.2 v0.35.1 v0.35.0; do
  echo "--- $tag ---"
  git show "$tag":requirements.txt 2>/dev/null | grep -E 'comfyui-workflow-templates|comfyui-frontend-package|comfyui-embedded-docs|comfy-kitchen|comfy-aimdo|av>|PyOpenGL' || echo "  (取不到)"
  echo "  日期: $(git log -1 --format=%ad --date=short "$tag" 2>/dev/null)"
done

echo
echo "############ 5. 镜像上 comfyui-workflow-templates 的实际最高版本 ############"
curl -s --max-time 25 http://mirrors.aliyun.com/pypi/simple/comfyui-workflow-templates/ 2>/dev/null \
  | grep -oE 'comfyui_workflow_templates-[0-9.]+' | sed 's/.*-//' | sort -V | tail -5

echo
echo "PROBE_PYPI_DONE"
