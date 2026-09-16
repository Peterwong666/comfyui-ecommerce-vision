"""工作流内核（B 流）—— `engine/`。

**一句话职责**：把「参数 Schema（契约 §3）+ 用户 params」渲染成
可直接提交给 ComfyUI 的 API 格式 JSON，并把元数据写进产物。

---

### 给 A 流（后端）的最小用法

```python
from engine import Registry, RenderOptions, render

registry = Registry.load()                 # workflows/registry.yaml
entry = registry.require("t2i_v1")          # 取某条工作流
definition = load_definition(registry.definition_path(entry))

result = render(definition, entry.schema, params, options=RenderOptions(
    asset_resolver=my_resolver,            # 只在用到 image 类参数时才需要
))
client.submit(result.workflow)             # 直接 POST /prompt
```

### 给 A 流的三个必须知道的点

1. **`params` 的合并优先级仍由 A 流负责**（契约 §3.4）。渲染器会用 `default` 兜底，
   所以传"已合并的完整参数"或"只传用户显式给的参数"都行，**结果一致（幂等）**。
2. **`-1` 的 seed 由渲染器解析成真实整数**，`result.seed` 就是该值 ——
   产物元数据必须记它，否则用户复现不出图（FR-5.5）。
3. **渲染失败抛 `RenderError`**，语义对应 EX-3 `invalid_param`，**不可重试**（T11）。
   不要把它归到可重试分支，否则队列会被必然失败的任务堵死。

### 设计约束

* **零第三方依赖**：只用标准库（`engine.registry` / `engine.validate` 读 YAML&CLI 时才需要 pyyaml）。
  这样 A 流导入 `engine` 不必关心后端虚拟环境装了什么。
* **不碰 `backend/`**：`engine/` 是独立的纯模块，不含任何 Web 框架或 ORM 依赖。
"""

from __future__ import annotations

from engine.errors import (
    MetadataError,
    RenderError,
    SchemaError,
    ValidationError,
    WorkflowEngineError,
)
from engine.metadata import (
    METADATA_JSON_KEY,
    METADATA_SCHEMA_VERSION,
    PROMPT_KEYS,
    build_metadata,
    read_png_metadata,
    read_png_text_chunks,
    write_png_metadata,
)
from engine.registry import (
    DEFAULT_REGISTRY_PATH,
    VALID_STATUS,
    VALID_VERIFICATION,
    Registry,
    WorkflowEntry,
)
from engine.render import (
    META_KEY,
    RenderOptions,
    RenderResult,
    Switch,
    derive_seed,
    load_definition,
    render,
    render_file,
)
from engine.schema import SCHEMA_VERSION, Field, ParamSchema, Target
from engine.transforms import TRANSFORMS, AssetResolver
from engine.validate import (
    SETTINGS_BOUNDS_MAPPING,
    Finding,
    Report,
    bounds_from_settings,
    check_settings_bounds,
    validate_registry,
)

__all__ = [
    # 异常
    "WorkflowEngineError",
    "SchemaError",
    "RenderError",
    "MetadataError",
    "ValidationError",
    # Schema（契约 §3）
    "SCHEMA_VERSION",
    "Field",
    "ParamSchema",
    "Target",
    # 渲染
    "META_KEY",
    "RenderOptions",
    "RenderResult",
    "Switch",
    "derive_seed",
    "load_definition",
    "render",
    "render_file",
    # transform
    "TRANSFORMS",
    "AssetResolver",
    # 注册表
    "DEFAULT_REGISTRY_PATH",
    "VALID_STATUS",
    "VALID_VERIFICATION",
    "Registry",
    "WorkflowEntry",
    # 元数据（P3-11）
    "METADATA_JSON_KEY",
    "METADATA_SCHEMA_VERSION",
    "PROMPT_KEYS",
    "build_metadata",
    "read_png_metadata",
    "read_png_text_chunks",
    "write_png_metadata",
    # 离线校验
    "Finding",
    "Report",
    "SETTINGS_BOUNDS_MAPPING",
    "bounds_from_settings",
    "check_settings_bounds",
    "validate_registry",
]
