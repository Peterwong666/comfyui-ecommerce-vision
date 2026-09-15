# 模型与依赖商用许可矩阵

> **这是商用项目的红线文档。** 状态：初稿（2026-09-15），随模型引入持续更新。
>
> 免责声明：以下信息为整理时的公开信息，**不具法律效力**。
> 每个模型下载后必须以仓库中的 LICENSE 原文为准，并登记到 `model_registry.yaml`。

## 三条铁律

1. **下载即登记** —— 拿到模型立刻记录：来源 URL、许可证名称、LICENSE 文件原文路径、hash。
2. **未知即不用** —— 找不到明确许可证的模型一律不用，找替代品；不存侥幸心理。
3. **发布前复查** —— 模型许可证会变更（尤其是社区模型），V1 发布前全量复查一次（P11-11）。

---

## 1. 引擎与框架

| 组件 | 许可证 | 可商用 | 说明 |
|---|---|---|---|
| **ComfyUI** | GPL-3.0 | ⚠️ 视使用方式 | 不改源码、不分发、仅以独立进程 API 调用 → 一般不触发传染；若 fork 修改并分发必须开源 |
| 自定义节点（各仓库） | 各异（Apache-2.0 / MIT / GPL-3.0 / 无声明） | ⚠️ 逐个核对 | 引入前必须登记许可，GPL 节点单独标记 |
| FastAPI / SQLAlchemy / Celery | MIT / BSD / BSD-3 | ✅ | 常规开源依赖 |
| React / Ant Design | MIT | ✅ | — |

## 2. 底模（Checkpoint）

| 模型 | 许可证 | 可商用 | 说明 |
|---|---|---|---|
| Stable Diffusion 1.5 | CreativeML OpenRAIL-M | ✅ | 附使用限制条款（禁非法内容、禁冒充、禁未经同意的肖像等） |
| SDXL 1.0 / Turbo | CreativeML OpenRAIL++-M | ✅ | 同上 |
| SD 3 / 3.5 | Stability AI Community License | ⚠️ 有条件 | 年收入 <100 万美元可免费商用，超出需商业许可 |
| **FLUX.1 [schnell]** | Apache-2.0 | ✅ | 商用最省心，推荐优先评估 |
| FLUX.1 [dev] | FLUX.1-dev Non-Commercial License | ❌ 禁止 | 需单独授权；**V1 不得用于商业交付** |
| 各类社区合并模型 / 蒸馏模型 | 多为 OpenRAIL 系或作者自定 | ⚠️ 高风险 | 社区模型常附加「禁转售」条款，必须逐个核对 |

## 3. 控制与适配

| 组件 | 许可证 | 可商用 | 说明 |
|---|---|---|---|
| ControlNet（lllyasviel） | 代码 Apache-2.0；权重多为 OpenRAIL | ✅ 通常允许 | 逐模型核对 |
| ControlNet Union / ProMax 系列 | 多为 OpenRAIL-M | ⚠️ 逐个核对 | — |
| IP-Adapter | Apache-2.0 | ✅ | — |
| T2I-Adapter | Apache-2.0 | ✅ | — |
| **InstantID** | 代码 Apache-2.0，但依赖 InsightFace 预训练模型 | ❌ 高风险 | InsightFace 权重为**非商用/研究用途**，商用需绕开 |
| PuLID | Apache-2.0 | ✅ 相对安全 | InstantID 的商用替代方案（V2 一致性场景优先评估） |
| SAM / SAM 2（Meta） | Apache-2.0 | ✅ | — |
| Grounding DINO | Apache-2.0 | ✅ | — |
| Florence-2（Microsoft） | MIT | ✅ | 自动遮罩方案的优选 |
| DWPose | 需逐个核对 | ⚠️ | 部分姿态权重仅研究用途 |
| BrushNet / PowerPaint | 需逐个核对 | ⚠️ | — |

## 4. 放大（Upscale）

| 模型 | 许可证 | 可商用 | 说明 |
|---|---|---|---|
| Real-ESRGAN | BSD-3-Clause | ✅ | — |
| 4x-UltraSharp、4x_NMKD 等社区放大模型 | 多为 OpenRAIL-M 或作者自定 | ⚠️ 逐个核对 | 在 Civitai / HuggingFace 上来源混杂 |

## 5. LoRA（风险最高的一类）

| 来源 | 常见许可证 | 可商用 | 说明 |
|---|---|---|---|
| Civitai | CreativeML OpenRAIL-M / 作者自定义 | ⚠️ 高风险 | **大量模型标注「禁止商用 / 禁止转售 / 需署名」**，是踩坑重灾区 |
| HuggingFace | 各异（Apache-2.0 / MIT / OpenRAIL / 无声明） | ⚠️ 逐个核对 | — |
| 官方 / 厂商发布 | 各异 | ⚠️ 逐个核对 | — |
| **自训练** | 自有 | ✅ | V2 微调闭环后可彻底规避此风险（E6） |

## 6. 输出内容本身的合规（不只是模型）

| 风险点 | 说明 | 应对 |
|---|---|---|
| 生成物版权 | 多数司法辖区下纯 AI 生成物不受著作权保护 | 商用前提示客户，不做版权承诺 |
| 商标与品牌 | 生成图中出现他人商标 / 卡通 IP | 提示词黑名单 + 上传素材授权声明 |
| 肖像权 | 使用真人照片做参考 / 生成真人形象 | 需授权声明；V1 内置上传即声明的流程 |
| 平台规范 | 电商主图有尺寸、水印、夸大宣传等限制 | 模板设计时遵循主流平台规范 |
| 生成标识 | 部分监管要求 AI 生成内容需标识 | V2 规划 C2PA 水印（E11） |

---

## 登记流程（每次引入模型时执行）

```bash
# 1. 记录来源
#    来源 URL、下载日期、发布者
# 2. 保存并核对 LICENSE 原文（不要只看许可证名称）
# 3. 计算 hash
sha256sum model.safetensors
# 4. 写入 model_registry.yaml
#    name / version / hash / source_url / license / commercial_ok / eval_score
# 5. 若 commercial_ok = unknown → 停止使用，找替代
```

## 复查记录

| 日期 | 复查范围 | 结论 | 处理 |
|---|---|---|---|
| 2026-09-15 | 建立矩阵初稿 | — | 待模型引入后逐条填充 |
