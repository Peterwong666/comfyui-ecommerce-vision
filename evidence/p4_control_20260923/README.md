# P4 控制体系 · 真实 GPU 实验证据（2026-09-23）

> 本目录是 `docs/sop/control_matrix.md` 中 P4-01/02/03/07/09/10 各表数字的**唯一证据来源**。
> 复算方式：`deploy/autodl/42_p4_control_experiments.py`（脚本自带指标算法，不依赖本目录的图）。

## 运行环境

| 项 | 值 |
|---|---|
| 实例 | autoDL RTX 4090 24GiB（`connect.westc.seetacloud.com:22910`） |
| 引擎 | ComfyUI v0.36.0（`--listen 127.0.0.1 --port 8188 --highvram`） |
| 底模 | `sd_xl_base_1.0.safetensors` |
| 采样 | dpmpp_2m / karras / steps=20 / cfg=6.5 / 1024×1024 |
| 参考图 | 本目录 `ref_product.png`（1024×1024 RGB，白色陶瓷马克杯） |
| 提示词 | 「perfume bottle on clean white background …」（与参考图**故意不同**，用于观察控制 vs 提示词的争夺） |
| 固定 seed | 20260923（一致性实验除外，见下） |

### 本轮修正的模型问题（关键）

| 模型 | 状态 | 说明 |
|---|---|---|
| `CLIP-ViT-bigG-14-*` | 可用于 bigG 系适配器 | 1.72G 参数 / 宽度 1664 |
| `CLIP-ViT-H-14-laion2B-s32B-b79K` | **本轮新下载** | 宽度 1280，`ip-adapter*_sdxl_vit-h` 必须用它 |
| `controlnet-{canny,depth}-sdxl-1.0` | 可用 | 虽为 **diffusers 格式**，ComfyUI `comfy/controlnet.py:739` 会做键名转换，实测可加载出图 |

> ⚠️ 上一轮会话把 bigG 编码器配给 vit-h 适配器，报
> `size mismatch for proj_in.weight: [1280,1280] vs [1280,1664]`，IP-Adapter 全部失败。本轮已修正。

## 复现命令（GPU 机 `/root/autodl-tmp` 下）

```bash
/root/miniconda3/bin/python 42_p4_control_experiments.py \
  --exp preprocess --exp cn-scan --exp ipa-scan --exp joint --exp consistency \
  --cn-kind canny,depth --weights 0.2,0.4,0.6,0.8,1.0 --n 10 \
  --ref /root/autodl-tmp/l4_assets/ref_product.png \
  --outdir /root/autodl-tmp/p4_out

# 第二轮（2026-09-23 晚）：补 P4 两条硬缺口 —— 姿态预处理器重评 + 材质一致性
/root/miniconda3/bin/python 42_p4_control_experiments.py \
  --exp synth-person --exp pose --exp material --n 10 --merge \
  --ref /root/autodl-tmp/l4_assets/ref_product.png \
  --outdir /root/autodl-tmp/p4_out
```

退出码 0 = 全部单元成功；`metrics.json.failures` 会给出失败单元数（两轮逐单元核对：**均为 0 失败**）。

## 产物清单

| 文件 | 对应任务 | 内容 |
|---|---|---|
| `preprocess/*.png` | P4-01 | 6 种预处理器在同一参考图上的控制图（Canny / LineArt / HED / DepthAnythingV2 / Openpose / DW） |
| `preprocess/_sheet.png` | P4-01 | 上面 6 张的联络表 |
| `cn_scan/_sheet_canny.png` | P4-02 | Canny 权重 0.0→1.0 的产物联络表 |
| `cn_scan/_sheet_depth.png` | P4-02 | Depth 权重 0.0→1.0 的产物联络表 |
| `ipa_scan/_sheet.png` | P4-03 | IP-Adapter 权重 0.2→1.0 的产物联络表 |
| `joint/_sheet.png` | P4-07 | canny×depth 四种配比 |
| `consistency/_sheet.png` | P4-09/10 | 同参考图同控制、仅变 seed 的 10 张 |
| `person/cand_*.png` | P4-06 前置 | 合成的全身人物候选 4 张（+ 各自 `_dw.png` 骨架图） |
| `person/_sheet_candidates.png` | P4-06 前置 | 4 个候选的联络表 |
| `ref_person.png` | P4-01 姿态重评 | 选定的人物参考图（1040×1216 级，全身站立） |
| `pose/_sheet.png` | P4-01 | 姿态预处理器：人物图 vs 商品图 × OpenPose/DW 四格对照 |
| `material/canny_only/_sheet.png` | P4-09/10 | 仅 canny@0.7、10 个 seed |
| `material/canny_ipa/_sheet.png` | P4-09/10 | canny@0.7 + IP-Adapter@0.6、同 10 个 seed |
| `material/_score_sheet.html` | P4 DoD | **人工评分表**（并列两组共 20 张；分数栏**为空**，须人填） |
| `p4_hard_gaps.log` | 第二轮 | 第二轮完整原始输出 |
| `metrics.json` | 全部 | 每个单元的 `status / elapsed_s / edge_f1 / delta / ssim / 颜色漂移` 原始数字（两轮已合并） |

## 本轮**已证明**与**未证明**

已证明（有产物 + 客观指标）：
- 6 种预处理器在本机可跑通并产出控制图（openpose/DW 对**非人物**商品图输出为空 —— 见下）
- Canny ControlNet 在 SDXL 上可加载并生成；`edge_f1` 随 strength 单调上升（0.011 → 0.232）
- IP-Adapter(vit-h) 修正编码器后可用；`ssim_vs_ref` 随 weight 上升（0.744 → 0.847）
- canny+depth 两路联合可同时挂载并出图
- 固定参考图 + canny@0.7 时，10 个 seed 的**结构**一致（成对 SSIM 0.8335）
- **（第二轮）姿态预处理器对人物图能检出骨架**：OpenPose 非黑占比 0.03303、DW 0.02282；
  同一节点对商品图输出 **0.0（空图）** ⇒ 2026-09-23 首轮「空转」的根因**确认是参考图没有人体**，不是节点坏
- **（第二轮）叠 IP-Adapter 能显著改善材质/颜色一致性**：颜色漂移 ΔE **6.36 → 3.77**（−40.7%）、
  色调直方图 Hellinger 距离 **0.121 → 0.028**（−76.5%）、成对 SSIM 0.8335 → 0.8556
- **（第二轮）同 seed 两次独立运行的产物像素逐字节一致**（`PIXELS_SAME`，见下「确定性口径」）

未证明（**不得**据此下结论）：
- ❌ **人工评分**：control_matrix 的 DoD「同一商品 10 次一致性人工评分 ≥4/5」**仍未做**，
  脚本只给 SSIM / 颜色漂移等机器代理；评分表 `material/_score_sheet.html` 已生成但**分数栏为空**
- ❌ 画质优劣：`edge_f1` / `ssim` / ΔE 只测「贴合度与稳定性」，不测好看与否
- ❌ lineart / softedge / openpose / dwpose 的 **ControlNet 权重甜区**：本机**没有**这四类 SDXL ControlNet 权重，
  只评了预处理器本身 ⇒ **姿态「控制」仍未评**（预处理器可用 ≠ 能控姿态）
- ❌ **材质一致性达标**：ΔE 从 6.36 降到 3.77 是**改善不是消除**，肉眼仍可辨（见两组联络表）；
  且只测了 `IP-Adapter@0.6` 一个权重点，未做权重扫描
- ❌ 商品**主体保真专项**（P4-10）：只有一张陶瓷杯参考图，不足以按品类做专项

### ⚠️ 「确定性」的口径（本轮澄清，重要）

第二轮复跑发现：与首轮**同 seed 同参数**的 10 张产物，**整文件 md5 全部不同**，一度像是不确定性。
实测结论是**元数据差异，不是像素差异**：

| 比较口径 | 结果 |
|---|---|
| 整文件 md5（`consistency/seedX.png` vs `material/canny_only/seedX.png`） | **10/10 DIFF** |
| 解码后**像素** sha256（抽样 3 个 seed） | **PIXELS_SAME** |
| 机器指标（成对 SSIM / 颜色漂移） | 两侧**完全一致**（0.8335 / 6.36） |

原因：ComfyUI `SaveImage` 会把 prompt 图（含每次新建的 `client_id` UUID）写进 PNG 的 tEXt 元数据，
故**文件字节必然不同，像素可以相同**。⇒ 项目里 P8-09 用的判据「**IDAT 逐字节一致**」是**正确**的，
拿整文件 md5 当确定性判据会得到假阴性。

> ⚠️ 另有一处**数字对不上**（如实标注）：「同参考图同控制、只变 seed」的成对 SSIM，
> `metrics.json` 首轮记录为 **0.8333 / min 0.7185 / max 0.9014**，
> 本轮在**同一批文件**上复算得 **0.8335 / 0.7188 / 0.9015**。
> 像素已证一致、复算可重复，故 2e-4 的差**我无法归因**（怀疑首轮运行时与现在所用 `scikit-image` 版本不同），
> 但量级不影响任何结论。**引用时以复算值 0.8335 为准**，并把这条当作待查的小异常记在台账里。

## 第二轮（2026-09-23 晚）· 补 P4 两条硬缺口

### ① 姿态预处理器重评（P4-01 / P4-06 前置）

首轮的 openpose/DW 两行是**空转**（参考图是马克杯、无人体，输出全黑）。
本机**没有任何人物素材**，故用**自家 t2i 现场合成**一张全身人物图（规避第三方素材许可问题）：
4 个候选各跑一次 DW 预处理器，取骨架占比最高者（seed 20260924，占比 0.02282）。

| 预处理器 | 人物参考图 非黑占比 | 商品参考图 非黑占比 | 判定 |
|---|---|---|---|
| `OpenposePreprocessor` | **0.03303** | **0.0** | 人物图**检出骨架**；商品图空图 |
| `DWPreprocessor` | **0.02282** | **0.0** | 同上 |

> **结论**：两个节点**本身是好的**，首轮全黑**纯粹因为参考图没有人**（`SKELETON_NONBLACK_MIN=0.005` 判据）。
> **但姿态「控制」仍未被证明** —— 本机没有 openpose/dwpose 的 SDXL ControlNet 权重，无法把骨架接到生成上。
> ⚠️ 因此 P4-06 的阻塞项**从「缺人物素材」修正为「缺 SDXL ControlNet 权重」**（前者已用合成图解掉）。

### ② 材质一致性：canny-only vs canny + IP-Adapter（P4-09/10）

首轮发现「canny 锁得住结构、锁不住材质」。本轮加一个 arm 回答**能不能把材质也锁住**。
两组 seed 区间与既有 `p4_09_consistency` 完全一致（20260923…20260932，N=10），可直接对比。

| arm | 成对 SSIM 均值 | SSIM min | 主体区 Lab 各通道 std (L,a,b) | **颜色漂移 ΔE↓** | **色调 Hellinger↓** | 主体面积比 |
|---|---|---|---|---|---|---|
| `canny@0.7`（对照） | 0.8335 | 0.7188 | 6.21 / 0.51 / 1.30 | **6.36** | **0.1210** | 0.48–0.87 |
| `canny@0.7 + IPA@0.6` | **0.8556** | **0.8126** | 3.72 / 0.21 / 0.55 | **3.77** | **0.0284** | 0.69–0.89 |

> **指标口径**：颜色漂移 ΔE = 每张图**主体掩码内 CIELAB 均值**在 seed 间的标准差向量的 L2 范数（越低越稳）；
> 色调距离 = 掩码内 32 桶归一化色调直方图两两平方 Hellinger 距离的均值（0 = 完全相同）。
> 主体掩码按**边框估背景色**取前景（**不是**白底阈值法 —— 白底法在本目录产物上退化成整图，面积比 ≈1.0）。
>
> **结论（数字 + 肉眼一致）**：
> - 结构：SSIM 0.8335 → **0.8556**（略好，且 **min 从 0.719 升到 0.813**，离群样本明显减少）
> - 材质/颜色：ΔE **−40.7%**、色调距离 **−76.5%** ⇒ **IP-Adapter 确实在锁材质，且效果显著**
> - 肉眼核验：`canny_only` 组杯内壁在「白/金/满金/橙」之间跳、seed 20260927 长出**裂纹釉面**、seed 20260932 **整只变金属金**；
>   `canny_ipa` 组**十张内壁一致为金色**、无裂纹、无整只变色
> - ⚠️ **是改善不是消除**：ΔE 3.77 仍可见；且只测了一个权重点（0.6），未做权重扫描

## 反直觉发现（值得记住）

1. **canny@0.7 单独锁不住材质，但加上 IP-Adapter 就锁得住大半**：10 个 seed 的轮廓/把手一直稳定，
   但杯内颜色在「白 / 金 / 满金」之间跳，s=20260927 甚至长出裂纹釉面。叠 `IPA@0.6` 后颜色漂移 ΔE 6.36→3.77、
   色调距离 0.121→0.028，肉眼上十张内壁一致为金色。**⇒ 结论修正为「结构 + 材质需双锚」**
   （CN 锁结构、IP-Adapter 锁材质），而不是首轮写的「canny 做不到，得靠 LoRA」。
   ⚠️ **注意这是"改善"不是"达标"**：ΔE 3.77 仍可见，且人工评分仍未做。
2. **depth@1.0 过约束**：`_sheet_depth.png` 的 w=1.0 把深度图的**等值线纹理**糊进了背景（左下角可见同心带），
   `edge_f1` 也随之从 0.6 的 0.159 掉到 1.0 的 0.047 —— 深度控制推荐上限约 0.6。
3. **参考图与提示词冲突时，控制权重就是"谁说了算"**：canny w≤0.4 出香水瓶（听提示词），w≥0.6 出马克杯（听控制）。
4. **「文件 md5 不同」不等于「输出不确定」**：ComfyUI 往 PNG 里嵌的 tEXt 元数据含每次新建的 `client_id` UUID，
   所以同 seed 两次跑**整文件必然不同、像素完全相同**。判断确定性要**比像素或 IDAT**，不要比整文件 md5。
5. **姿态预处理器的"空图"是参考图的问题、不是节点的问题**：同一个 `DWPreprocessor`，
   对商品图输出 0.0 非黑、对合成的人物图输出 0.02282 —— 换图即好，一行都没改。
