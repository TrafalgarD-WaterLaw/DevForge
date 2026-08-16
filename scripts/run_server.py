"""DevForge 服务启动脚本（重构 Phase 6 起的新入口）。

用法（项目根目录）:
    python scripts/run_server.py

等价于旧入口 python -m devforge.server.app：插入 src 到 sys.path 后
调用 serving.interfaces.app:main()。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from serving.interfaces.app import main  # noqa: E402

if __name__ == "__main__":
    main()
