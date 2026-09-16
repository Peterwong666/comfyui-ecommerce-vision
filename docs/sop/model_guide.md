# 模型目录规范与接入流程（P5-01）

> 目标：让「模型放在哪、叫什么、怎么加」**只有一种答案**。
> 本文件与 [`license_matrix.md`](./license_matrix.md)（许可）、[`../../engine/model_registry.yaml`](../../engine/model_registry.yaml)（登记）构成模型资产的三件套：
> **放得对** → **许可清** → **可追溯**。
>
> **最后核实**：2026-09-16 ｜ 对应环境：autoDL RTX 4090 + ComfyUI v0.36.0（`deploy/versions.lock`）

---

## 1. 心智模型：磁盘两处，一处是真相

```
物理存储（真相）                        ComfyUI 视图（软链）
/root/autodl-tmp/comfyui-data/          /root/ComfyUI/
├── models/          ← 唯一真相        ├── models/      → 软链到上面
├── output/                            ├── output/      → 软链
└── input/                             └── input/       → 软链
```

**为什么这样**：autoDL 系统盘仅 30G（模型装不下），`/root/autodl-tmp` 是 50G 独立数据盘。
迁移由 `deploy/autodl/02_migrate_models.sh` 完成，**幂等**。

| 规则 | 说明 |
|---|---|
| **R1** | **永远不要把权重写进系统盘** `/root/ComfyUI/models/` —— 那是软链名字，实体会落到数据盘 |
| **R2** | 判断软链是否完好：`ls -ld /root/ComfyUI/models` 应显示 `-> /root/autodl-tmp/comfyui-data/models` |
| ⚠️ **R3** | **`git checkout -f` 会重建真实目录顶掉软链** —— 升级/切分支前必须按 `12_upgrade_checkout.sh` 的流程「摘链 → checkout → 校验 → 挂回」（`项目进展.md` #16 处置 3） |
| **R4** | 权重一律 `.gitignore` 拦截，**仓库只保留登记表**（ADR-002） |

---

## 2. 目录结构与用途

⚠️ **以下是 2026-09-16 服务器实测的真实结构**（`ls -l /root/autodl-tmp/comfyui-data/models/`，共 37 个子目录），
不是「理想的」结构 —— 其中既有 ComfyUI 默认目录，也有历史遗留目录。

> ### ⚠️ 先看这一条：别被 `extra_model_paths.yaml` 骗了
>
> `/root/ComfyUI/extra_model_paths.yaml` 配置了三组路径：
> `base_path: /root/autodl-tmp/models`、`sys: /root/models`、`fs: /root/autodl-fs/models`，
> 启动日志会打印一批 `Adding extra search path ...`。
> **但实测 `/root/autodl-tmp/models` 与 `/root/models` 都不存在** —— 这是**死配置**。
> **模型的实际位置只有一处**：`/root/autodl-tmp/comfyui-data/models/`（经 `/root/ComfyUI/models` 软链）。
> 排查「模型找不到」时不要在这些死路径上浪费时间。

```
/root/autodl-tmp/comfyui-data/models/        ← 唯一真相（经 /root/ComfyUI/models 软链可见）
├── checkpoints/          底模全集            [在用] sd_xl_base_1.0.safetensors
├── unet/                 纯 UNet             [在用] flux-2-klein-4b.safetensors
├── vae/                  独立 VAE            [在用] flux2-vae · sdxl_vae_fp16fix
├── text_encoders/        文本编码器          [在用] qwen_3_4b · qwen_3_4b_fp4_flux2(禁用)
├── diffusion_models/     新式命名的 UNet 目录（与 unet/ 并存）
├── clip/                 CLIP 系（SD1.5/2.x 时代命名）
├── clip_vision/          CLIP 视觉塔          [空]  ← IP-Adapter 依赖，P4-03 需要
├── loras/                LoRA                 [空]  ← P5-03 待建
├── controlnet/           ControlNet           [空]  ← P5-04 待建
├── ipadapter/            IP-Adapter 权重      [空]  ← P4-03 待建
├── upscale_models/       放大器               [空]  ← P5-05 待建
├── sams/                 SAM / SAM2           [空]  ← P4-05 待建
├── BiRefNet/             BiRefNet 抠图权重    [空]  ← P4-05 待建
├── pulid/                PuLID                [空]  ← V2 评估
├── grounding-dino/       Grounding DINO       [仅 .cfg.py，无权重]
├── insightface/          ⚠️ **非商用权重**    [有] buffalo_l/*.onnx ← 红线，任何链路不得引用
├── ultralytics/          检测器               [有] face_yolov8n.pt ← 许可待核实
├── embeddings/           文本反演（hypernetworks 也指向此目录）
├── style_models/         风格模型
├── model_patches/        模型补丁
├── vae_approx/           TAESD 系（预览用）
├── latent_upscale_models/ 潜空间放大器
├── configs/              模型配置（checkpoints 类也指向此目录）
├── audio_encoders/       音频编码器（V1 不用）
├── bert-base-uncased/    BERT（部分文本节点用）
├── gligen/               GLIGEN
├── mmdets/               MMDetection
├── onnx/                 ONNX 模型
├── diffusers/            diffusers 格式模型
├── animatediff_models/   动画（V1 不用）
├── animatediff_motion_lora/
├── liveportrait/         人像动画（V1 不用）
├── photomaker/           PhotoMaker（V2 评估）
├── tensorrt/             TRT engine 缓存 ⚠️ 可能占大量系统盘
└── audio_encoders/       音频
```

> ⚠️ **实测磁盘余量（2026-09-16）**：系统盘 `/` 30G 已用 20G，**剩 11G**；
> 数据盘 `/root/autodl-tmp` 50G 已用 28G，**剩 23G**。
> `tensorrt/` 一旦编译 engine 会显著增长 —— 这也是白名单禁用
> `ComfyUI-Upscaler-Tensorrt` 的附带理由（`deploy/comfyui/node_whitelist.yaml` §G）。

### 2.1 各目录的命名前缀约定

**本项目的命名约定**（不改变 ComfyUI 的查找逻辑，只约束我们自己的文件命名）：
`<来源>_<模型族>_<变体>_<精度>.<ext>`

| 段 | 取值示例 | 说明 |
|---|---|---|
| 来源 | `sd` / `sdxl` / `flux2` / `qwen` / `community` | 模型族归属，便于一眼看出能否商用 |
| 模型族 | `xl` / `klein` / `3` | 具体版本 |
| 变体 | `base` / `distilled` / `turbo` / `fp16fix` | 关键区分（**`base` 与 `distilled` 混用会直接导致步数/cfg 用错**，见 §5） |
| 精度 | `fp16` / `fp4` / `fp8` | 量化版必须显式标注 |

**已就位的实际文件**（保持现状，不再改名 —— 改名会破坏 `versions.lock` 与工作流引用）：

| 现有文件 | 符合约定？ | 备注 |
|---|---|---|
| `checkpoints/sd_xl_base_1.0.safetensors` | ✅ | — |
| `unet/flux-2-klein-4b.safetensors` | ⚠️ | 文件名**未体现 `distilled`** —— 极易与 Base 版混淆，见 §5 |
| `vae/flux2-vae.safetensors` | ✅ | — |
| `vae/sdxl_vae_fp16fix.safetensors` | ✅ | `community` 前缀缺省（历史文件） |
| `text_encoders/qwen_3_4b.safetensors` | ✅ | 全精度，klein 链路唯一可用 |
| `text_encoders/qwen_3_4b_fp4_flux2.safetensors` | ✅ | 精度已标注，但**已禁用**（shape mismatch） |

> **纪律：已在 `versions.lock` 中登记的文件名一律不改。**
> 改名 = 破坏 `sha256` 锚点 + 破坏所有工作流引用 + 破坏历史产物元数据的可复现性。
> 命名约定只约束**新引入**的文件。

---

## 3. 新模型怎么放（接入清单）

按顺序执行，**任何一步不过就停**：

| # | 步骤 | 命令 / 动作 | 不过会怎样 |
|---|---|---|---|
| 1 | **查许可** | 先查 [`license_matrix.md`](./license_matrix.md) 是否已有结论；没有就去仓库读 LICENSE 原文 | 未知许可的模型进了交付链路 = 法律风险 |
| 2 | **确认能下到** | `curl -sI <url> \| head -1` 看是否 200 | 许可允许但 gated（403）= 事实上不可用（FLUX.1 schnell 的教训） |
| 3 | **下载到数据盘** | `aria2c -c -x 16 -s 16 -k 1M --dir=<数据盘目录> -o <文件名> <url>` | 下到系统盘会塞满 30G |
| 4 | **判断下载真的完成** | **只看 `.aria2` 文件是否消失**，不看文件大小 | `-k 1M` 会预分配，文件大小恒为最终值，**大小不可信**（`项目进展.md` #10/#11） |
| 5 | **算 hash 与体积** | `sha256sum <file>; stat -c %s <file>` | 无 hash 则 M2「销毁重建」验证不成立 |
| 6 | **登记注册表** | 写入 `engine/model_registry.yaml`（字段见该文件头部注释） | 未登记 = 模型事实上不存在，后台管理看不到 |
| 7 | **更新版本锁** | 重跑 `deploy/autodl/01_lock_versions.sh`，回填 `deploy/versions.lock` 的 `[models]` 段 | 环境不可复现 |
| 8 | **验证能加载** | `08_probe_nodes.py` 确认节点可见该模型；必要时跑一次最小工作流 | 静默失败会在生产时才炸 |
| 9 | **补齐软链** | 确认 `/root/ComfyUI/models/<kind>/` 能看到文件（软链天然覆盖，一般无需操作） | 模型「放进去了但 ComfyUI 看不见」 |
| 10 | **回写文档** | 上表 §2.1 的实际文件清单 + 许可矩阵「复查记录」 | 下一个人会重复踩坑 |

### 3.1 严禁事项

| 严禁 | 原因 |
|---|---|
| ❌ 用 `models/` 这样的**非锚定**路径写进 `.gitignore` | 会连 `backend/app/models/` 一起忽略（`项目进展.md` #21 真实事故） |
| ❌ 手动改**已在 `versions.lock` 中**的文件名 | 破坏 hash 锚点与历史产物可复现性 |
| ❌ 用 `find` 猜模型目录 | #11 的误判来源；用 `ls -l` 看软链或读 `versions.lock` |
| ❌ 把 `fp4`/`fp8` 量化版当成「省显存的免费午餐」 | klein 的 fp4 文本编码器隐藏维不匹配、直接加载失败（#13） |
| ❌ 在同一 ComfyUI 实例切换不同底模**却不改 worklow 参数** | klein 是 4 步/cfg=1，SDXL 是 25-30 步/cfg 7-8，混用必然出错图 |

---

## 4. 版本归档与回滚（P5-07 预留）

| 项 | 规范 |
|---|---|
| **同 name 多版本** | `model_registry.yaml` 中同名条目用 `version` 区分，`status` 只允许一个 `active` |
| **归档目录** | `<数据盘>/models/.archive/<name>/<version>/` （**注意：`.archive` 前缀保证 ComfyUI 不会扫描到**） |
| **回滚动作** | ① 改 registry 里两个条目的 `status` ② 工作流参数里的模型名改回旧版 ③ 重跑冒烟 |
| **存储策略** | 数据盘 50G，当前模型约 26.6GB。**.archive 总量上限 20GB**，超出时按「最久未使用」淘汰 |
| **删除前提** | 必须先确认该版本**没有任何 active 工作流引用**（`workflows/registry.yaml` 反查） |

> ⚠️ **不要删除 `qwen_3_4b_fp4_flux2`** —— 它虽禁用，但删了要重下 3.6GB；磁盘真正告急时优先删它。

---

## 5. ⚠️ 本项目已踩过的三个模型相关坑（务必先读）

### 5.1 文件名不含 `base` / `distilled` → 步数用错

`flux-2-klein-4b.safetensors` 是**蒸馏版**（Distilled），官方模板里它对应 **4 步 / cfg=1**；而 `-base-` 是另一个文件（需常规步数）。
判据来源：`项目进展.md` #16 验证 3（展开官方模板 `image_flux2_klein_text_to_image.json` 得到）。
**后果**：若误当 Base 版跑 20+ 步，速度优势全失且画面会退化。

### 5.2 fp4 量化文本编码器与全精度路径不互通

`qwen_3_4b_fp4_flux2.safetensors` 隐藏维 **1280**，ComfyUI 的 `Qwen3_4B` 期望 **2560** → `CLIPLoader` 直接 shape mismatch。
**纪律**：FLUX.2 链路**一律用全精度 `qwen_3_4b.safetensors`**，不要为省显存换量化版（`项目进展.md` #13/#14）。

### 5.3 「许可允许但下载不到」= 不可用

FLUX.1 schnell 是 Apache-2.0（可商用），但官方仓库 gated（403）取不到。
**选模型必须把「能否真正下载到」当成与许可同级的硬指标**（`项目进展.md` #7）。

---

## 6. 显存与加载策略

| 项 | 当前值 | 依据 |
|---|---|---|
| GPU | RTX 4090 / 24564 MiB（实际可见 24081 MB） | `versions.lock` `[gpu]` |
| 启动参数 | `--highvram`（**必锁**） | `项目进展.md` #17/#18：v0.36.0 默认开启 dynamic VRAM，会让 SDXL 劣化 1.58x |
| 双底模常驻 | 13.1 GB / 24 GB | `项目进展.md` #18 |
| 单次出图峰值 | 15.8 GB / 24 GB（klein 链路实测） | `项目进展.md` #16 验证 5 |
| 并发 | **恒为 1**（NFR-2） | 显存与一致性双重考虑 |
| ⚠️ 红线 | 若未来同时常驻模型总量逼近 24G，**必须重新评估 `--highvram`**（它不卸载模型） | `03_start_comfyui.sh` 注释 |

---

## 7. 与其它文档/代码的接口

| 对象 | 关系 |
|---|---|
| `deploy/versions.lock` `[models]` | **环境权威**。sha256 与 size 的唯一来源，本规范不改它，只指向它 |
| `engine/model_registry.yaml` | **资产权威**。许可、评测分、状态、显存占用 |
| `docs/sop/license_matrix.md` | **许可权威**。判决与替代方案 |
| DB 表 `model_registry`（`backend/app/models/registry.py:24`） | registry.yaml 的落库目标，字段一一对应 |
| `docs/sop/contracts.md` §6 | 本文件的归属边界：C 流可改 `assets/` 与 `engine/model_registry.yaml`；`engine/` 其它文件归 B 流 |

---

## 8. 待补齐项

| # | 事项 | 归属 | 状态 |
|---|---|---|---|
| 1 | `sd_xl_base_1.0` 的实际下载渠道未留痕 | 需回查下载记录后补登 registry | ⬜ 未做 |
| 2 | ~~数据盘实际占用未核对~~ | — | ✅ **2026-09-16 实测**：系统盘剩 11G / 数据盘剩 23G；6 个模型的 `file_size` 与 `versions.lock` 逐条一致 |
| 3 | `.archive` 归档脚本尚未实现 | P5-07 | ⬜ 未做 |
| 4 | ~~`sams/` `ultralytics/` 等目录内容未核实~~ | — | ✅ **2026-09-16 实测**：`sams/` `BiRefNet/` `loras/` `controlnet/` `upscale_models/` `ipadapter/` `clip_vision/` `pulid/` 均为空；`ultralytics/` 有 `face_yolov8n.pt`；`insightface/` 有 buffalo_l（**红线权重**） |
| 5 | **`ultralytics/face_yolov8n.pt` 的许可未登记** | C 流（P4-05 前） | ⬜ 新增 |
| 6 | **`extra_model_paths.yaml` 的死路径应清理**（指向不存在的 `/root/autodl-tmp/models` 与 `/root/models`） | 环境维护 | ⬜ 新增（易误导排查） |
