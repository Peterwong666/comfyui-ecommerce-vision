# 快速上手（15 分钟从零到出图）

> 目标：在**不读其他文档**的情况下，从「什么都没开」走到「浏览器里出一张图」。
> 需要深入了解、排错、加模型、跑基准 → 看 [`runbook.md`](./runbook.md)。
>
> 适用环境：autoDL 上的 RTX 4090 实例 + 你本机（Linux）。最后核实：2026-09-16。

---

## 0. 心智模型：只有两个状态

```
你本机                      autoDL 实例（两种模式二选一）
┌──────────────┐            ┌────────────────────────────┐
│ 浏览器        │            │ 有卡模式：能出图，按 GPU 计费 │
│   ↓ 隧道      │  ──SSH──▶  │ 无卡模式：¥0.1/h，不能出图   │
│ 终端          │            │ 关机：0 计算费，数据保留      │
└──────────────┘            └────────────────────────────┘
```

- **要出图 = 必须有卡模式。**
- **切换模式必须先关机**（会中断所有进程），且切回有卡时可能没卡可用。
- ComfyUI 只监听 `127.0.0.1:8188`，**所以浏览器访问必须走 SSH 隧道**，直接开公网地址是打不开的。

---

## 1. 前置条件（一次性配置）

| # | 事项 | 怎么确认 |
|---|---|---|
| 1 | 本机装了 `sshpass` | `which sshpass` 有输出即可；没有就 `sudo apt-get install -y sshpass` |
| 2 | 有 autoDL 实例密码 | 在 autoDL 控制台设置/查看（⚠️ **不要写进仓库任何文件**） |
| 3 | 知道实例地址 | `connect.westc.seetacloud.com`，端口 `22910`，用户 `root` |

把密码放进当前终端的环境变量（**只对当前窗口有效，不入库**）：

```bash
export SSHPASS='你的实例密码'
```

---

## 2. 五步跑通

### 第 1 步 · 评估 GPU 并开机

打开 autoDL 控制台实例列表（西北B区 / 329机）：

```bash
# 需要浏览器手动操作，无命令行替代
https://www.autodl.com/console/instance/list
```

**先看 GPU 空闲数再点开机**：

| 控制台显示 | 能否开机 | 说明 |
|---|---|---|
| `GPU空闲/总量：7/10` | ✅ 可以 | 有卡模式能拿到卡 |
| `GPU空闲/总量：0/10` | ❌ 别点 | 卡被占满，开了也用不到 GPU，白付钱 |

- **要出图** → 点最右侧「开机」（默认就是有卡模式），确认费用后开机，等 1-2 分钟
- **只写代码/下载** → 点「更多」→「无卡模式开机」（无需等资源，随时可开）

### 第 2 步 · 启动 ComfyUI

```bash
sshpass -e ssh -o StrictHostKeyChecking=no -p 22910 root@connect.westc.seetacloud.com \
  'bash /root/autodl-tmp/03_start_comfyui.sh'
```

看到 `就绪，耗时约 N 次轮询` 加一段 `system_stats` JSON 就是起来了。
（已在运行时会直接返回「ComfyUI 已在运行」，不会重复启动。）

### 第 3 步 · 在你本机建隧道

```bash
sshpass -e ssh -N \
  -o StrictHostKeyChecking=no -o ServerAliveInterval=20 -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:8188:127.0.0.1:8188 \
  -p 22910 root@connect.westc.seetacloud.com
```

这条命令会**一直挂在前台**（这是正常的）。另开一个终端继续。

**判断隧道通没通**：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8188/system_stats   # 期望 200
```

### 第 4 步 · 浏览器出图

1. 打开 **<http://127.0.0.1:8188>**
2. 把工作流 JSON 拖进画布（本机仓库里的文件）：
   - `workflows/flux2_klein_t2i_v1.json` —— **推荐先试这个**，FLUX.2 klein 蒸馏版，4 步，约 **1.8s/张**
   - `workflows/t2i_v1.json` —— SDXL，30 步，约 **5s/张**
3. 在 `CLIPTextEncode`（klein）或正向提示词节点里改文字
4. 点 **Run / 队列提示词**
5. 出图落在服务器 `/root/autodl-tmp/comfyui-data/output/`，网页右侧会直接显示

> 想批量改参数或看结构化输出 → 用 API 方式，见 [`runbook.md` 第 6 节](./runbook.md#6-出图的三条路)。

### 第 5 步 · 收工

```bash
# 1) 关闭本地隧道：在隧道那个终端按 Ctrl-C
# 2) 去 autoDL 控制台点「关机」—— 0 计算费（数据盘存储费仍计）
```

⚠️ **关机不要超过 15 天**，否则实例会被 autoDL 释放。

---

## 3. 一页速查卡

把 `<SCRIPTS>` 当作远端脚本目录（默认 `/root/autodl-tmp`）。

| 我要做什么 | 命令 |
|---|---|
| 连服务器看状态 | `sshpass -e ssh -p 22910 root@connect.westc.seetacloud.com 'uptime; nvidia-smi'` |
| 启动 ComfyUI | `... 'bash <SCRIPTS>/03_start_comfyui.sh'` |
| 按生产配置重启 | `... 'bash <SCRIPTS>/31_restart_prod.sh'` |
| 看启动日志 | `... 'tail -30 /root/autodl-tmp/comfyui.log'` |
| 建隧道（本机） | `sshpass -e ssh -N -L 127.0.0.1:8188:127.0.0.1:8188 -p 22910 root@connect.westc.seetacloud.com` |
| 工作流静态校验（**提交前必做**） | `... 'python <SCRIPTS>/26_validate_workflow.py <wf>.json'` |
| API 冒烟出图 | `... 'python <SCRIPTS>/04_api_smoke_test.py <wf>.json /root/out'` |
| 性能基准 | `... 'python <SCRIPTS>/07_bench_workflow.py <wf>.json -n 12 --warmup 1 --steps 30'` |
| 查节点入参 | `... 'python <SCRIPTS>/08_probe_nodes.py -d <节点类名>'` |

**当前性能基线**（生产配置 `--highvram`，N=12 warmup=1）：

| 工作流 | median | P95 | 地板(min) |
|---|---|---|---|
| SDXL 1024²/30 步 | 5.00s | 5.48s | **4.59s** |
| FLUX.2 klein 4 步 | 1.83s | 2.67s | **1.52s** |

> 容量规划看**地板**，SLA 承诺看 **P95**。理由见 [`runbook.md` 第 7 节](./runbook.md#7-性能基线的正确口径)。

---

## 4. 红线（违反会出事故，不是建议）

| # | 红线 | 为什么 |
|---|---|---|
| 1 | **不要把实例密码写进任何仓库文件** | 仓库是开源的；密码已在本项目对话中出现过，**建议尽快在控制台改掉** |
| 2 | **不要让 ComfyUI 监听 `0.0.0.0`** | ComfyUI 无鉴权，暴露公网 = 把 GPU 送给别人 |
| 3 | **远端多步逻辑一律写成 `.sh` 上传后执行，不要内联进 ssh 命令** | 已两次翻车：一次是转义静默生成 `$LOCK` 脏文件，一次是 `pkill -f` 把执行它的 shell 自己杀了 |
| 4 | **商用交付禁用 InsightFace 权重链路**（如 InstantID） | 自带权重为非商用；替代方案是 PuLID（Apache-2.0）。详见 [`license_matrix.md`](./license_matrix.md) |

---

## 5. 卡住了

| 现象 | 先做什么 |
|---|---|
| 浏览器打不开 `127.0.0.1:8188` | 按第 3 步的 `curl` 判断是隧道问题还是 ComfyUI 没起来 |
| ssh 连不上（Connection refused） | 实例是关机状态 → 去控制台开机 |
| 开机要了钱但没有卡 | 开机前没看 GPU 空闲数 → 关机，等有资源再开 |
| 出图报错、节点缺失 | `26_validate_workflow.py` 静态校验 + `08_probe_nodes.py` 查节点 |
| 其他 | [`runbook.md` 第 12 节排错手册](./runbook.md#12-排错手册) |
