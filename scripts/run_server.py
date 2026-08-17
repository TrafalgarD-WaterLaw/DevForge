"""DevForge 服务启动脚本（重构 Phase 6 起的新入口）。

用法（项目根目录）:
    python scripts/run_server.py

等价于旧入口 python -m devforge.server.app：插入 src 到 sys.path 后
调用 serving.interfaces.app:main()。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Windows 控制台默认 GBK：agent print ⚠/emoji 等 unicode 会抛
# UnicodeEncodeError 把整个 run 崩掉 —— 统一 UTF-8 输出 + replace 兜底
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from serving.interfaces.app import main  # noqa: E402

if __name__ == "__main__":
    main()
