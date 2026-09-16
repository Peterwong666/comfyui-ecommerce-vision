# 详细操作说明（Runbook）

> 本文件是**开发环境的操作手册**：日常怎么开、怎么跑、怎么排错、脚本各是干什么的。
> 只想先跑起来 → 看 [`quickstart.md`](./quickstart.md)。
>
> 环境事实的最后核实：**2026-09-16**。若你读到时已过去较久，请以 `deploy/versions.lock`
> 与 `项目进展.md` 为准，并回来更新本文件。

---

## 目录

1. [环境总览](#1-环境总览)
2. [脚本位置与同步约定](#2-脚本位置与同步约定)
3. [模式与成本的决策口径](#3-模式与成本的决策口径)
4. [连接服务器](#4-连接服务器)
5. [ComfyUI 生命周期](#5-comfyui-生命周期)
6. [出图的三条路](#6-出图的三条路)
7. [性能基线的正确口径](#7-性能基线的正确口径)
8. [工作流：从本地到服务器](#8-工作流从本地到服务器)
9. [新增模型](#9-新增模型)
10. [版本锁与可复现](#10-版本锁与可复现)
11. [后端服务](#11-后端服务)
12. [排错手册](#12-排错手册)
13. [脚本索引](#13-脚本索引)
14. [文档维护纪律](#14-文档维护纪律)
15. [红线清单](#15-红线清单)

---

## 1. 环境总览

### 1.1 两侧的资产分布

| | 本机（你的开发机） | 远端（autoDL 实例） |
|---|---|---|
| 角色 | 写代码、跑后端与测试、**浏览器入口** | 跑 ComfyUI 推理（唯一有 GPU 的地方） |
| 代码仓库 | `~/py/comfyUI_project`（git 仓库，权威） | `/root/ComfyUI`（引擎源码）+ 脚本副本 |
| 模型 | ❌ 不存 | `/root/autodl-tmp/comfyui-data/models/` |
| 产物 | 需要时 scp 回来 | `/root/autodl-tmp/comfyui-data/output/` |

**关键原则：模型与产物一律不进口仓库**（`.gitignore` 已挡住 `*.safetensors` / `output/` / `input/`）。

### 1.2 远端关键路径（背下来）

| 路径 | 内容 |
|---|---|
| `/root/ComfyUI` | ComfyUI 源码，v0.36.0（commit `ee71d5c4`） |
| `/root/autodl-tmp` | 50G 数据盘，**所有大文件都在这** |
| `/root/autodl-tmp/comfyui-data/models/` | 模型：`checkpoints/ unet/ vae/ text_encoders/` |
| `/root/autodl-tmp/comfyui-data/output/` | 出图产物 |
| `/root/autodl-tmp/comfyui.log` | ComfyUI 运行日志（**排错第一现场**） |
| `/root/autodl-tmp/` | 本项目脚本的副本（见第 2 节） |
| `/root/miniconda3/bin/python` | 跑 ComfyUI 的 Python 3.12 |

⚠️ **系统盘只有 30G**（现剩约 11G），数据盘 50G（剩约 23G）。
**任何新下载的东西都要放数据盘**，否则系统盘会满。

⚠️ `models` / `input` / `output` 是**软链**到数据盘的。任何 `git checkout` 都会重建真实目录、
**顶掉软链、让模型"消失"** —— 升级/切换版本前必须读 `deploy/autodl/12_upgrade_checkout.sh` 的摘链流程。

### 1.3 已安装的模型（`versions.lock` 里有 sha256）

| 文件 | 大小 | 说明 |
|---|---|---|
| `checkpoints/sd_xl_base_1.0.safetensors` | 6.46G | SDXL 底模 |
| `unet/flux-2-klein-4b.safetensors` | 7.22G | FLUX.2 klein 4B，**蒸馏版：4 步 / cfg=1** |
| `vae/flux2-vae.safetensors` | 0.31G | klein 配套 VAE |
| `vae/sdxl_vae_fp16fix.safetensors` | 0.31G | SDXL 配套 VAE |
| `text_encoders/qwen_3_4b.safetensors` | 7.49G | klein 文本编码器，**必须用全精度版** |
| `text_encoders/qwen_3_4b_fp4_flux2.safetensors` | 3.58G | ⚠️ 历史遗留，**已无用**（见第 12 节） |

---

## 2. 脚本位置与同步约定

### 2.1 权威在上游，远端是副本

```
本机 deploy/autodl/xxx.sh     ← 唯一事实来源（进 git）
        │  scp
        ▼
远端 <远端脚本目录>/xxx.sh    ← 执行用的副本
```

推送方式：

```bash
scp -P 22910 deploy/autodl/<脚本> root@connect.westc.seetacloud.com:<远端脚本目录>/
```

### 2.2 ⚠️ 已知不一致（待清理）

历史上脚本被先后推到过**两个目录**，目前**同时存在**：

| 远端路径 | 引用它的脚本 |
|---|---|
| `/root/autodl-tmp/` | `29_` `30_` `31_` 中的 `31_restart_prod.sh`、以及所有手工执行的命令 |
| `/root/` | `27_gpu_verify.sh` 内部调用 `/root/03_start_comfyui.sh` |

**影响**：改了 `03_start_comfyui.sh` 只推到一处，两处行为会不一致（例如生产配置 `--highvram` 没同步过去）。

**临时纪律**：当前以 **`/root/autodl-tmp/` 为准**；推送 `03_start_comfyui.sh` 时**两处都推**。

**核对命令**（下次开机时跑一次，确认现状）：

```bash
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  'ls -la /root/*.sh /root/autodl-tmp/*.sh /root/autodl-tmp/*.py 2>/dev/null'
```

**根治方案**（待办）：统一到 `/root/autodl-tmp/scripts/`，并把 `27_gpu_verify.sh` 的硬编码路径改成相对脚本自身目录。

### 2.3 系统盘空间紧张 → 脚本放数据盘

脚本本身很小，但**日志会膨胀**（`comfyui.log` 已 40KB+ 且每次启动重写）。日志与产物都留在数据盘。

---

## 3. 模式与成本的决策口径

计费规则、三种模式对比、省钱纪律的完整版见 [`gpu_cost_strategy.md`](./gpu_cost_strategy.md)。这里只给**判断口径**：

### 3.1 一句话决策

> **需要 GPU 的攒起来一次性切，不需要 GPU 的一律在无卡模式做。**

因为切换模式**必须先关机**，频繁切换既费时间（每次 1-2 分钟）又有「切回来没卡」的风险。

### 3.2 什么需要 GPU，什么不需要

| 不需要 GPU（无卡模式 ¥0.1/h 就能做） | 需要 GPU（必须有卡） |
|---|---|
| 下载模型、git 操作、装依赖 | ComfyUI 出图 / 冒烟测试 |
| 写文档、写代码、跑后端与单元测试 | 模型加载与显存验证 |
| 工作流 JSON 编辑 + 静态校验（`26_`） | 性能基准（`07_`）与回归 A/B |
| CPU 模式节点审计（`23_`） | golden set 评测、P9 压测 |

### 3.3 每天收工

**关机，不要挂着空转**（0 计算费；只有数据盘存储费）。但**关机不超过 15 天**否则实例被释放。

---

## 4. 连接服务器

### 4.1 前置

```bash
export SSHPASS='<实例密码>'    # 仅当前终端；切勿写入任何文件
```

### 4.2 直连执行

```bash
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 -p 22910 \
  root@connect.westc.seetacloud.com '<命令>'
```

### 4.3 端口隧道（浏览器访问的前提）

ComfyUI 只监听 `127.0.0.1:8188`，**且不应该改成 0.0.0.0**（无鉴权）。要访问就用 SSH 本地转发：

```bash
sshpass -e ssh -N \
  -o StrictHostKeyChecking=no \
  -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:8188:127.0.0.1:8188 \
  -p 22910 root@connect.westc.seetacloud.com
```

| 参数 | 作用 |
|---|---|
| `-N` | 只做转发，不开 shell |
| `ServerAliveInterval/CountMax` | 网络抖动时保活，避免静默断开 |
| `ExitOnForwardFailure` | 本地 8188 被占时**立即报错退出**，而不是"连上了但转发没生效" |

**验证链路**：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8188/system_stats   # 200 = 通
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8188/               # 200 = 前端页在
```

**想让隧道常驻**（默认被当前终端绑住，终端关了就断）：

```bash
setsid nohup sshpass -e ssh -N -o StrictHostKeyChecking=no -o ServerAliveInterval=20 \
  -o ExitOnForwardFailure=yes -L 127.0.0.1:8188:127.0.0.1:8188 \
  -p 22910 root@connect.westc.seetacloud.com > /tmp/comfy_tunnel.log 2>&1 < /dev/null &
```

### 4.4 常见连接故障

| 现象 | 原因 | 处置 |
|---|---|---|
| `Connection refused` | 实例处于**关机**状态 | 去控制台开机（先看 GPU 空闲数） |
| 连上了但一直卡在密码提示 | 没有 `sshpass -e`，或 `SSHPASS` 未导出 | `export SSHPASS=...` 后用 `sshpass -e ssh` |
| 握手成功但要求 `publickey,password` | 服务器**只支持密码登录**，没配密钥 | 正常，用密码即可 |
| 隧道报 `Address already in use` | 本地 8188 已被占（旧隧道没退干净） | `ss -ltnp \| grep 8188` 找到 PID 后杀掉 |
| 浏览器打开是白页 / 000 | 隧道断了或 ComfyUI 没起来 | 先跑 §4.3 的两条 curl 定位是哪一侧 |

---

## 5. ComfyUI 生命周期

### 5.1 启动（生产配置）

```bash
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  'bash /root/autodl-tmp/03_start_comfyui.sh'
```

脚本行为：
- 已在运行 → 打印现状并退出（**幂等**，不会起第二个实例）
- 未运行 → `setsid nohup` 后台启动，日志写 `/root/autodl-tmp/comfyui.log`，轮询就绪（最多 180s）

**启动参数**：`COMFY_EXTRA_ARGS`，默认 **`--highvram`**。

> ⚠️ **不要去掉 `--highvram`**。v0.36.0 起 dynamic VRAM 在 Nvidia 上默认开启，
> 会让 SDXL 单张从 4.6s 劣化到 7.3s（1.58x）。这是实测结论，见 `项目进展.md` #17/#18。
> 临时覆盖：`COMFY_EXTRA_ARGS='' bash 03_start_comfyui.sh`（仅用于对照实验）。

### 5.2 健康检查与「生效开关」核对

```bash
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  'curl -s http://127.0.0.1:8188/system_stats | /root/miniconda3/bin/python -c \
   "import json,sys; d=json.load(sys.stdin); print(d[\"system\"][\"comfyui_version\"], d[\"system\"][\"argv\"])"'
```

日志里的关键三行（**这才是性能的真正决定因素**）：

```
[INFO] Set vram state to: HIGH_VRAM                      ← 期望：HIGH_VRAM
[INFO] Using async weight offloading with 2 streams
[INFO] Using pytorch attention                            ← 升级前后一直是这样
```

**不应再出现** `DynamicVRAM support detected and enabled`。

### 5.3 重启

```bash
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  'bash /root/autodl-tmp/31_restart_prod.sh'
```

该脚本会停旧实例 → 按生产配置重启 → 核对生效开关 → 打印 `argv`。

### 5.4 停止

> ⚠️ **不要**在 ssh 命令里内联 `pkill -f "main.py --listen"`！
> `pkill -f` 匹配完整命令行，而远端执行它的那个 `bash -c` 命令行里**就含这个字符串**，
> 结果是**把执行它的 shell 自己杀掉**，服务被杀后没人重启。本项目已两次踩到这个坑。

正确姿势之一（**锚定正则**，只匹配真正的 python 进程）：

```bash
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  "pkill -f '^/root/miniconda3/bin/python main\.py'"
```

正确姿势之二（拿 PID 再杀，最稳）：

```bash
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  'pgrep -f "ComfyUI/main.py" | xargs -r kill'
```

更好的做法：**把这类逻辑写进 `.sh` 再执行**，父进程命令行不含模式串，天然安全。

### 5.5 看日志

```bash
# 启动段（含生效开关）
... 'grep -aE "VRAM|attention|offload|Total" /root/autodl-tmp/comfyui.log | head -20'
# 尾部（报错现场）
... 'tail -40 /root/autodl-tmp/comfyui.log'
```

---

## 6. 出图的三条路

### 6.1 网页（交互调试首选）

隧道通了之后打开 <http://127.0.0.1:8188>，拖入 `workflows/*.json`。

- 产物在 `/root/autodl-tmp/comfyui-data/output/`，网页会自动展示
- 适合调提示词、试参数、看效果
- ❌ 不适合批量与可复现（参数不出现在版本历史里）

### 6.2 API 冒烟（脚本化验证）

```bash
WD=/root/autodl-tmp
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  "/root/miniconda3/bin/python $WD/04_api_smoke_test.py $WD/flux2_klein_t2i_v1.json /root/autodl-tmp/smoke_out"
```

会打印 `[submit] prompt_id=...` → `[result] 耗时=... 完成状态=success` → `[image] <路径>`。

> ⚠️ **首次出图包含模型加载**（约 15-20s），这不是稳态性能 —— 判读性能请看第 7 节。

### 6.3 性能基准（要数字时用）

```bash
WD=/root/autodl-tmp
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  "/root/miniconda3/bin/python $WD/07_bench_workflow.py $WD/t2i_v1.json \
   -n 12 --warmup 1 --steps 30 --outdir /root/autodl-tmp/bench_out"
```

参数（`07_bench_workflow.py`）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `<workflow.json>` | 必填 | 工作流路径 |
| `-n` | 12 | 计入统计的次数 |
| `--warmup` | 1 | **预热次数，不计入统计** —— 见第 7 节 |
| `--steps` | 无 | 覆盖 KSampler 的 steps |
| `--seed-base` | 20260915 | 每次运行自动改 seed，避免结果被缓存 |
| `--outdir` | `bench_out` | 产物目录 |

输出：每个样本耗时 + `min / mean / median / P95 / max / stdev` + 原始样本列表。

---

## 7. 性能基线的正确口径

这一节是踩坑换来的，**不看会得出错误结论**。

### 7.1 三种口径，别混用

| 口径 | 是什么 | 用途 |
|---|---|---|
| **冷启动** | 启动后**第一次**出图 | 决定用户首次请求的等待体验 |
| **稳态 median/P95** | warmup 之后的样本统计 | SLA 承诺、产品性能描述 |
| **稳态地板（min）** | 稳态样本里的最小值 | **容量规划、判断自己有没有回归** |

**冷启动与稳态差 3-4 倍**：SDXL 冷启动约 15-20s（含 6.9GB 权重读盘 + 显存分配），稳态 median 5.00s。
历史上一度把 12.0s（冷启动）当成稳态写进 PRD，导致吞吐被低估 2.6 倍、单张成本被高估 —— 见 `项目进展.md` #15。

### 7.2 为什么用「地板」而不是 median

autoDL 实例是**共享宿主机**（128 核、load average 常年在 19 左右，多容器共用磁盘与内存带宽）。
基准里存在**周期性干扰尖峰**（每约 3 个样本一次），而且 —— **升级前的基线里同样有**：

| 运行 | 地板 | 尖峰 |
|---|---|---|
| 升级前基线 v0.3.75 | 4.54s | 5.05~5.07s |
| 修复后 `--highvram` | 4.59s | 5.25~5.48s |
| 未修复（默认） | 6.36s | 7.57~7.72s |

median 会被尖峰出现频率的随机波动带偏。**判读"我这次改动有没有让模型变慢"要用地板**；
单纯看 median 会把宿主机噪声误判成自己的回归（`项目进展.md` #18）。

### 7.3 基准操作纪律

1. **必须带 `--warmup`**，且预热样本不计入统计
2. **同一配置至少复测两轮**，单轮不足以定论（实测同配置跨轮可差 0.3s）
3. **改性能后必须复测另一个模型**：修 SDXL 不能拿 klein 当代价
4. **数字必须带口径**：冷启动/稳态、分辨率、步数、N、是否含模型加载

### 7.4 当前基线（生产配置 `--highvram`，N=12 warmup=1，1024²）

| 工作流 | 地板 | median | P95 |
|---|---|---|---|
| SDXL 30 步 | **4.59s** | 5.00s | 5.48s |
| FLUX.2 klein 4 步 | **1.52s** | 1.83s | 2.67s |

显存：两个底模同时常驻约 **13.1GB / 24GB**。

---

## 8. 工作流：从本地到服务器

### 8.1 位置约定

| | 路径 |
|---|---|
| 权威（进 git） | `workflows/*.json` |
| 远端（执行用） | `/root/autodl-tmp/*.json` |

```bash
scp -P 22910 workflows/flux2_klein_t2i_v1.json root@connect.westc.seetacloud.com:/root/autodl-tmp/
```

### 8.2 提交前必做：静态校验（零 GPU 机时）

```bash
WD=/root/autodl-tmp
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  "/root/miniconda3/bin/python $WD/26_validate_workflow.py $WD/flux2_klein_t2i_v1.json"
```

它会检查（不花 GPU 机时）：

- 节点类型在当前引擎里是否存在
- 必填入参是否齐备、入参名是否被接受
- 连线目标与输出序号是否越界
- 枚举取值是否合法

期望输出 `VALIDATE=PASS`。**这一步能在开机前就拦住结构性错误**，是省机时最有效的一招。

### 8.3 查节点入参

```bash
... '/root/miniconda3/bin/python /root/autodl-tmp/08_probe_nodes.py CLIPLoader'      # 列出匹配的节点类
... '/root/miniconda3/bin/python /root/autodl-tmp/08_probe_nodes.py -d CLIPLoader'   # 打印完整入参 schema
```

### 8.4 FLUX.2 klein 的坑（照抄，别自己拼）

| 项 | 正确做法 | 错误做法 |
|---|---|---|
| 文本编码节点 | `CLIPTextEncode` + `CFGGuider` + `ConditioningZeroOut` | ❌ `CLIPTextEncodeFlux`（它要 `t5xxl` 键，klein 链路里不存在） |
| 文本编码器 | `qwen_3_4b.safetensors`（全精度） | ❌ `qwen_3_4b_fp4_flux2.safetensors`（架构不匹配） |
| 采样 | 4 步 / cfg=1（**我们下的是蒸馏版**） | ❌ 按 base 版设 20+ 步 |

**有官方模板时不要自己拼工作流。** `comfyui-workflow-templates` 包里就有
`image_flux2_klein_text_to_image.json`，节点组合与参数直接可用。

---

## 9. 新增模型

### 9.1 三条铁律

1. **下到数据盘**：`/root/autodl-tmp/comfyui-data/models/`
2. **走 hf-mirror**：huggingface.co 直连不通（0 B/s），用 `hf-mirror.com`（约 3MB/s）
3. **下载前先校验文件清单**：checkpoint / text_encoder / VAE **三类缺一不可**（已因缺 VAE 踩过一次）

### 9.2 判断下载完成的正确方法

```bash
# ✅ 正确：看 .aria2 控制文件是否消失 + 日志出现 Download complete
... 'ls /root/autodl-tmp/comfyui-data/models/unet/*.aria2 2>/dev/null; \
     tail -3 /root/autodl-tmp/dl_*.log'
```

> ❌ **不要看文件大小**。aria2c 用 `-k 1M` **预分配**磁盘空间，`ls -la` 会显示"已下满"，
> 而实际可能只下了 29%（本项目真实踩过，见 `项目进展.md` #10）。

### 9.3 断点续传

无卡模式随时可能重启，**下载脚本必须带 `aria2c -c`**。重启后重跑同一脚本即可续传。

### 9.4 下完之后

1. **登记 sha256**：重跑 `01_lock_versions.sh`（见第 10 节），把新模型写进 `[models]`
2. **核对商用许可**：更新 [`license_matrix.md`](./license_matrix.md)
3. 用 `08_probe_nodes.py` + 静态校验确认工作流认得它
4. **最后才上 GPU 验证** —— 顺序别颠倒：本项目曾先下了 11GB 才发现引擎版本不支持

---

## 10. 版本锁与可复现

### 10.1 `deploy/versions.lock` 的结构

| 段 | 内容 | 为什么需要 |
|---|---|---|
| `[host]` `[gpu]` `[python]` | OS / 驱动 / 显存 / torch+CUDA | 基础环境 |
| `[comfyui]` | 版本 tag + commit | 引擎版本 |
| **`[launch]`** | **启动参数 + 从日志解析出的生效运行时开关** | ⚠️ **性能的真正决定因素** |
| **`[models]`** | **6 个模型文件的 size + sha256** | M2 验收「销毁重建仍能出同一张图」的锚点 |
| `[custom_nodes]` | **32 个**自定义节点的 commit | 节点生态 |
| `[pip]` | `pip freeze` 全量 | 依赖 |

> ⚠️ **只锁包版本不够。** v0.3.75 → v0.36.0 的升级里，包版本全锁死也复现不出旧性能 ——
> 因为**新版本改了默认值**（dynamic VRAM 默认开）。这就是 `[launch]` 段存在的理由。

### 10.2 什么时候必须重做

- 升级引擎版本 / torch
- 改启动参数（如调整 `COMFY_EXTRA_ARGS`）
- 新增或替换模型文件
- 装/卸自定义节点

### 10.3 怎么做

```bash
# 在服务器上（⚠️ 会哈希 15GB 模型，约 5-10 分钟，且占磁盘 I/O，不要与基准测试并行）
sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com \
  'bash /root/autodl-tmp/01_lock_versions.sh'
# 生成到 /root/autodl-tmp/comfyui-data/versions.lock
```

然后取回本机：

```bash
scp -P 22910 root@connect.westc.seetacloud.com:/root/autodl-tmp/comfyui-data/versions.lock deploy/
```

临时跳过哈希：`HASH_MODELS=0 bash 01_lock_versions.sh`

---

## 11. 后端服务

后端在 `backend/`，**跑在本机，不需要 GPU，也不需要连服务器**。

### 11.1 环境

```bash
cd backend
.venv/bin/python --version      # 3.12.x
```

依赖声明在 `backend/pyproject.toml`。

### 11.2 启动

默认配置连 PostgreSQL（`postgresql+psycopg://platform:platform@127.0.0.1:5432/platform`）。
**本机没起 PG 时用 SQLite 兜底**（实测可用）：

```bash
cd backend
DATABASE_URL="sqlite:///./dev.db" \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","app":"电商视觉 AIGC 量产平台","version":"0.1.0","env":"dev"}
```

交互式 API 文档：<http://127.0.0.1:8000/docs>

**现有 18 条路由**：`/health`、`/health/ready`、`/api/v1/auth/{register,login,me}`、
`/api/v1/tasks[/{id}][/estimate][/cancel][/retry]`、`/api/v1/batches[/{id}][/retry-failed]`、
`/api/v1/workflows[/{name}/schema]`、`/api/v1/templates[/categories]`、`/api/v1/models`

### 11.3 测试与检查

```bash
cd backend
.venv/bin/python -m pytest -q          # 31 passed
.venv/bin/ruff check app tests         # All checks passed
```

### 11.4 已知缺口

| 缺口 | 影响 | 状态 |
|---|---|---|
| **`mypy` 已声明但未安装** | 类型检查这条防线是空的 | 待补装 |
| **`alembic/versions/` 为空** | 无初始迁移，无法对真实 PG 建表 | 待生成 |
| 缺 ER 图 | PRD §9 自评「不足」 | 待补并回写 PRD |
| 启动时警告 `jwt_secret 仍为默认值` | 生产必须覆盖 | 已有防护，需在 `.env` 设真实值 |

### 11.5 常用环境变量（见 `.env.example`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | PG 连接串 | 本地开发可改 SQLite |
| `REDIS_URL` / `CELERY_BROKER_URL` | `redis://127.0.0.1:6379/…` | 队列 |
| `COMFYUI_BASE_URL` | `http://127.0.0.1:8188` | 引擎地址 |
| `LOG_JSON` | `true` | 生产 JSON 日志，本地可关 |

> 配置里有**一条主动防护**：`COMFYUI_BASE_URL` 不允许写成 `0.0.0.0`，否则启动即报错（NFR-4）。

---

## 12. 排错手册

> 每条都是真实发生过的。完整分析过程见 [`项目进展.md`](../../项目进展.md) 的问题处置台账。

### 12.1 连接与隧道

| 现象 | 原因 | 处置 |
|---|---|---|
| `Connection refused` | 实例已关机 | 控制台开机（先看 GPU 空闲数） |
| `EAI_AGAIN` / DNS 解析失败 | 本机网络问题 | 检查网络；本机连 HF API 也不通是已知情况 |
| 隧道报 `Address already in use` | 本地 8188 被占 | `ss -ltnp \| grep 8188`，杀旧进程 |
| 页面对了但出图无响应 | ComfyUI 挂了 | 看 `comfyui.log` 尾部 |

### 12.2 出图与节点

| 现象 | 原因 | 处置 |
|---|---|---|
| `KeyError: 't5xxl'` | 用了 `CLIPTextEncodeFlux`，或引擎版本太旧 | 改用 `CLIPTextEncode + CFGGuider + ConditioningZeroOut`（§8.4） |
| `size mismatch ... [1024, 1280] vs [1024, 2560]` | 文本编码器用了 fp4 版 | 换 `qwen_3_4b.safetensors` 全精度版 |
| `undefined symbol: _ZN3c106detail14torchCheckFailEPKcS2_jRKSs` | C++ 扩展（flash-attn / SageAttention）与 torch ABI 不匹配 | 卸载该扩展；ComfyUI 会自动回退原生注意力，**不影响功能** |
| `infer_schema: Parameter stride has unsupported type list[int]` | torch 版本低于 v0.36.0 要求 | 需 torch ≥ 2.7（20 系以上要 cu130+） |
| 节点在 UI 里找不到 | 节点属于失效的自定义节点 | 见 12.4 |

### 12.3 性能

| 现象 | 原因 | 处置 |
|---|---|---|
| 出图比预期慢 1.5 倍以上 | 没加 `--highvram`，dynamic VRAM 生效 | 用 `31_restart_prod.sh` 按生产配置重启 |
| 数字波动很大 | 共享宿主机的干扰尖峰 | 用**地板**判读，并复测第二轮（§7） |
| 首次出图要 15-20s | 冷启动，含模型加载 | 正常；判性能要用稳态口径 |
| 想恢复注意力加速 | 需要重编译 flash-attn（1-3h，易失败） | **不建议**；原生注意力已满足 V1 指标 |

### 12.4 自定义节点失效（升级遗留）

| 节点 | 原因 | 处置 |
|---|---|---|
| `nunchaku` | torch ABI 变化 | 按 torch 2.13 重装；不在 V1 关键路径，可先禁用 |
| `TeaCache` | 依赖的 `precompute_freqs_cis` 已被移除 | 查上游是否有新版；否则禁用 |
| `smZNodes` | diffusers 相关 | 非必需，直接禁用 |

### 12.5 磁盘与内存

| 现象 | 原因 | 处置 |
|---|---|---|
| 系统盘告急（<5G） | 大文件下到了系统盘 | 迁到 `/root/autodl-tmp`；脚本一律显式指定数据盘路径 |
| 无卡模式 OOM | 只有 2GB 内存 / 0.5 核 | 一次只跑一个任务；docker-compose 必须到有卡模式 |
| git 操作被 OOM kill | git 默认吃大量内存 | 加护栏：`pack.threads=1`、`pack.windowMemory=32m`（见 `11_fetch_target.sh`） |
| 模型"消失"了 | `git checkout` 重建真实目录顶掉了软链 | 按 `12_upgrade_checkout.sh` 的摘链流程恢复 |

### 12.6 文档与流程

| 现象 | 原因 | 处置 |
|---|---|---|
| `ls <路径>` 返回空，以为目录不存在 | **路径写错时 `ls` 不报错、只返回空** | 用 `find -name "*.safetensors"` 反查；路径要从脚本里读，不要凭记忆 |
| Edit 工具报 `String to replace not found` | 基于记忆构造的 `old_string` 已过期 | 先 `Read` 目标区段，再基于原文构造 |
| ssh 命令返回空 + 退出码 0 | 可能把执行它的 shell 自己杀了（`pkill -f` 自匹配） | 见 §5.4；**零输出本该立刻引起怀疑** |

---

## 13. 脚本索引

`deploy/autodl/` 共 30+ 个脚本。**绝大多数是升级期间的一次性考古脚本**，日常只用其中几个。

### 13.1 日常会用（★ 常用）

| 脚本 | 用途 |
|---|---|
| ★ `03_start_comfyui.sh` | 启动 ComfyUI（幂等，默认 `--highvram`） |
| ★ `31_restart_prod.sh` | 按生产配置重启 + 生效开关核对 |
| ★ `26_validate_workflow.py` | **提交前**静态校验工作流（零 GPU 机时） |
| ★ `04_api_smoke_test.py` | API 冒烟出图 |
| ★ `07_bench_workflow.py` | 性能基准（带 warmup 与统计） |
| ★ `08_probe_nodes.py` | 查节点类与入参 schema |
| ★ `01_lock_versions.sh` | 采集/重做版本锁 |
| `02_migrate_models.sh` | 把 models/input/output 迁到数据盘并软链（已执行过） |

### 13.2 模型下载

| 脚本 | 用途 |
|---|---|
| `05_download_flux2_klein.sh` | 下载 FLUX.2 klein 三件套 |
| `06_resume_tenc_download.sh` | 续传全精度文本编码器 |
| `watch_flux2_download.sh` | 轮询下载状态（klein） |
| `watch_flux2_full_te.sh` | 轮询下载状态（全精度 TE） |
| `probe_safetensors_keys.py` | 只读 safetensors 头部，识别包含哪些组件 |

### 13.3 引擎升级（v0.3.75 → v0.36.0，一次性，已完成）

> 这些脚本是升级过程的可复现记录，**平时不用再跑**。若将来再次升级，可作模板参考。

| 脚本 | 用途 |
|---|---|
| `09_recon_upgrade.sh` | 升级前只读侦察（回退锚点、本地改动、软链隐患） |
| `10_fetch_and_inspect.sh` / `11_fetch_target.sh` | 拉取目标版本（带内存护栏），不改工作树 |
| `12_upgrade_checkout.sh` | 切代码到目标版本（**含软链保护流程**） |
| `13_install_deps.sh` / `14_dryrun_target_deps.sh` | 装依赖 / 先预演不落地 |
| `15_probe_pypi.sh` | 探测包源可达性与备选版本 |
| `16_diag_torch_incompat.sh` / `18_diag_import_chain.sh` | 定位 `list[int]` 注册失败的来源与导入链 |
| `17_candidate_versions.sh` / `19_probe_torch_path.sh` / `20_probe_torch_target.sh` | 选 torch 目标版本、判定升级路径 |
| `21_dryrun_torch.sh` / `22_upgrade_torch.sh` | 预演 / 执行 torch 升级 |
| `23_cpu_mode_node_audit.sh` | **CPU 模式**节点审计（不耗 GPU 就能查 import 级故障） |
| `24_fix_flash_attn.sh` | 修复 flash-attn ABI 失配的连锁导入失败 |
| `25_verify_klein_route.sh` | 确认 klein 的 TE 路由到 flux2 |

### 13.4 验证与性能实验

| 脚本 | 用途 |
|---|---|
| `27_gpu_verify.sh` | 切回有卡后的一次过验证（5 项） |
| `28_try_sage_attention.sh` | 尝试用 SageAttention 追性能（**已证失败**：ABI 失配） |
| `29_ab_offload_vram.sh` | 单变量 A/B，定位回归到具体开关 |
| `30_verify_prod_config.sh` | 生产配置收口 + 复测另一底模 |

---

## 14. 文档维护纪律

### 14.1 三份文档的分工

| 文档 | 定位 | 记什么 |
|---|---|---|
| `todolist.md` | **计划** | 要做哪些事（checkbox） |
| `项目进度.md` | **基线度量** | 里程碑在哪、偏差多少、一屏速览 |
| `项目进展.md` | **纪实** | 实际发生了什么、遇到什么问题、**怎么分析怎么解决** |

### 14.2 问题台账的六段格式

`项目进展.md` 里每个问题都按此结构记录：

```
现象 → 分析 → 决策 → 处置 → 验证 → 沉淀
```

**为什么**：本项目的价值一半在「过程可证明」。遇到问题后**如何分析、如何取舍、如何绕开**，
本身就是能力证据，比功能列表更有说服力。

### 14.3 三条即时纪律

1. **每完成一个动作、每解决一个障碍，立即登记**，不要攒着批量更新
2. **任务完成同步打勾**：`todolist.md` 打勾 + `项目进度.md` 更新百分比
3. **口径一致**：任务数、百分比在三份文档里必须对得上（发现不一致要立即校正并说明原因）

### 14.4 ⚠️「做完」的定义 = 已提交 + 已登记

本项目**真实吃过这个亏**：后端骨架建好后因会话中断，既没提交也没进文档，
在版本历史与进度文档里**同时不存在**（`项目进展.md` #20）。

> 因此：**产出一块工作后，立即提交并登记。** 别指望"待会儿一起弄"。

### 14.5 发现遗留工作怎么办

**先验证再登记**，不要凭目录完整度判断完成度。
核实动作至少包括：跑测试、跑 lint、实际启动一次、核对文档口径 —— 这些往往能查出
"看起来完成了"掩盖掉的真实缺口（如本次查出的 ruff 问题、缺失的 mypy、空的 `alembic/versions/`）。

---

## 15. 红线清单

| # | 红线 | 后果 | 依据 |
|---|---|---|---|
| 1 | **不要把实例密码写进仓库任何文件** | 仓库开源，密码泄露 | 密码已在对话中出现，**建议尽快在控制台改掉** |
| 2 | **不要把 ComfyUI 监听改成 `0.0.0.0`** | 无鉴权，等于把 GPU 公开送人 | NFR-4 |
| 3 | **远端多步逻辑一律写成 `.sh` 上传后执行** | 内联 ssh 已两次翻车（转义生成 `$LOCK`、`pkill` 自杀） | `项目进展.md` #3 / #19 |
| 4 | **`pkill -f` 不要用会匹配到自身的模式串** | 会把执行它的 shell 杀掉 | `项目进展.md` #19 |
| 5 | **商用交付禁用 InsightFace 权重链路（InstantID）** | 该权重为非商用 | [`license_matrix.md`](./license_matrix.md) |
| 6 | **模型/产物/密钥不入仓库** | `.gitignore` 已挡；绕过需明确理由 | `.gitignore` 原则 |
| 7 | **判性能必须看地板 + 带 warmup** | 会被宿主机噪声或冷启动值误导 | `项目进展.md` #15 / #18 |
| 8 | **判下载完成只看 `.aria2` 消失 + `Download complete`** | 文件大小被预分配伪装成"已下满" | `项目进展.md` #10 |
| 9 | **`git checkout` 前先确认软链保护** | 会切断数据盘模型挂载，模型全部"消失" | `项目进展.md` #16 |
| 10 | **关机不超过 15 天** | 实例被 autoDL 释放 | [`gpu_cost_strategy.md`](./gpu_cost_strategy.md) |
