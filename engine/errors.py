"""工作流内核（B 流）的异常体系。

分四类而不是一个 `EngineError`，是为了让**调用方（A 流）能区分处置方式**：

| 异常 | 语义 | A 流应如何处理 |
|---|---|---|
| `SchemaError` | 注册表里的参数 Schema 本身写错了 | **配置故障**，不该在请求路径上重试；记日志 + 拒绝发布该工作流 |
| `RenderError` | 参数或节点图无法渲染成合法 API 格式 | 对应 EX-3 `invalid_param`，**不可重试**（T11） |
| `MetadataError` | 元数据写入产物失败 | 对应 EX-13 `save_failed`，**可重试** |
| `ValidationError` | 离线校验未通过（L1/L2） | 开发期故障，不进生产路径 |

对齐 `contracts.md` §1.5 的 `ErrorType`：`RenderError` → `invalid_param`（不重试），
`MetadataError` → `save_failed`（可重试）。把"参数写错"误判为可重试，
会让队列被必然失败的任务堵死（PRD §5.4 T7 vs T11 的设计要点）。
"""

from __future__ import annotations


class WorkflowEngineError(Exception):
    """工作流内核所有异常的基类。"""


class SchemaError(WorkflowEngineError):
    """参数 Schema 不符合 `contracts.md` §3 —— 属配置错误，不是用户错误。"""


class RenderError(WorkflowEngineError):
    """无法把「Schema + params」渲染成合法的 ComfyUI API 格式。

    对应 EX-3 `invalid_param`：**不可重试**（重试一万次还是错）。
    """


class MetadataError(WorkflowEngineError):
    """产物元数据写入失败。对应 EX-13 `save_failed`：**可重试**。"""


class ValidationError(WorkflowEngineError):
    """离线校验（L1/L2）未通过。

    不直接用于生产路径，但 `engine.validate` 会把收集到的问题归到这一类抛出。
    """
