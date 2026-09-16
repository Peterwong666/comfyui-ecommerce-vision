"""`python -m engine` 的入口。

**存在的唯一理由：让用户不要看到 runpy 的警告。**

`python -m engine.validate` 会触发
`RuntimeWarning: 'engine.validate' found in sys.modules after import of package 'engine'`
—— 因为 `engine/__init__.py` 已经导入了 `engine.validate`，而 runpy 随后又要把它
当作 `__main__` 再执行一次。这不是 bug，但**看起来像 bug**，会让人怀疑工具本身有问题。

`python -m engine` 不经过这条路径：先导入包，再执行本模块，因此没有警告。

    PYTHONPATH=. python -m engine            # 校验（等价于旧的 -m engine.validate）
    PYTHONPATH=. python -m engine --json     # 机器可读输出

（`python -m engine.validate` 仍然可用，只是会打印那条警告，保留它以免打断既有脚本。）
"""

from __future__ import annotations

import sys

from engine.validate import main

if __name__ == "__main__":
    sys.exit(main())
