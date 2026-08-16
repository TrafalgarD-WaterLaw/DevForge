"""DevForge 重构迁移工具 —— 按映射表机械替换全仓 import（dry-run / apply）。

用法（项目根目录）:
    python scripts/rewrite_imports.py            # 只报告将改动的文件与命中数
    python scripts/rewrite_imports.py --apply    # 实际写入

映射表按"先具体后宽泛"顺序执行（"from x.y" 必须先于 "x.y" 宽泛规则）。
docs/ 是历史记录，web/ 是前端，均不扫描。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 迁移映射：core 层 + infrastructure 叶子 + 宽泛兜底（先具体后宽泛）
REWRITE_RULES: list[tuple[str, str]] = [
    # Phase 1 — core 层
    ("from devforge.config.loader", "from core.config"),
    ("from devforge.pipeline.hooks", "from core.events"),
    ("from devforge.server.logging_setup", "from core.logging"),
    ("from devforge.server.connection import get_current_run",
     "from core.context import get_current_run"),
    ("from devforge.server.connection import set_current_run",
     "from core.context import set_current_run"),
    ("import devforge.config.loader", "import core.config"),
    ("import devforge.pipeline.hooks", "import core.events"),
    ("import devforge.server.logging_setup", "import core.logging"),
    # Phase 2 — infrastructure 叶子
    ("from devforge.tools.registry", "from codegen.infrastructure.tools.registry"),
    ("from devforge.agents.llm_client", "from codegen.infrastructure.llm_client"),
    ("import devforge.tools.registry", "import codegen.infrastructure.tools.registry"),
    ("import devforge.tools.", "import codegen.infrastructure.tools."),
    ("import devforge.agents.llm_client", "import codegen.infrastructure.llm_client"),
    # 宽泛规则：字符串打补丁目标（monkeypatch.setattr("devforge...")）等
    ("devforge.config.loader", "core.config"),
    ("devforge.pipeline.hooks", "core.events"),
    ("devforge.server.logging_setup", "core.logging"),
    ("devforge.tools.registry", "codegen.infrastructure.tools.registry"),
    ("devforge.tools.", "codegen.infrastructure.tools."),
    ("devforge.agents.llm_client", "codegen.infrastructure.llm_client"),
    # Phase 3 — memory 域
    ("from devforge.memory.store", "from memory.infrastructure.chroma_store"),
    ("from devforge.memory.format", "from memory.interfaces.prompt_formatter"),
    ("from devforge.memory.extract", "from memory.domain.extract"),
    ("from devforge.memory.models", "from memory.domain.models"),
    ("import devforge.memory.", "import memory."),
    ("devforge.memory.", "memory."),
    # Phase 4 — codegen domain（注意："phase" 与 "phases" 前缀冲突，
    # 只用 from/import 具体形式 + 带点的宽泛形式）
    ("from devforge.pipeline.blackboard", "from codegen.domain.blackboard"),
    ("from devforge.pipeline.registry", "from codegen.domain.registry"),
    ("from devforge.pipeline.validate", "from codegen.domain.validate"),
    ("from devforge.pipeline.phase import", "from codegen.domain.phase import"),
    ("from devforge.agents.agent", "from codegen.domain.agent"),
    ("import devforge.pipeline.blackboard", "import codegen.domain.blackboard"),
    ("import devforge.pipeline.registry", "import codegen.domain.registry"),
    ("import devforge.pipeline.validate", "import codegen.domain.validate"),
    ("import devforge.pipeline.phase\n", "import codegen.domain.phase\n"),
    # 注意：禁止裸 "import devforge.pipeline.phase" 规则 —— 它是复数
    # "devforge.pipeline.phases" 的前缀，曾误伤 5 个文件（Phase 4 教训）
    ("import devforge.agents.agent", "import codegen.domain.agent"),
    ("devforge.pipeline.blackboard", "codegen.domain.blackboard"),
    ("devforge.pipeline.registry", "codegen.domain.registry"),
    ("devforge.pipeline.validate", "codegen.domain.validate"),
    ("devforge.pipeline.phase.", "codegen.domain.phase."),
    ("devforge.agents.agent", "codegen.domain.agent"),
    # Phase 5 — codegen application（specific 在前，wide 兜底在后）
    ("from devforge.pipeline.phases.demand",
     "from codegen.application.phases.requirements_discussion"),
    ("from devforge.pipeline.phases.document",
     "from codegen.application.phases.documentation"),
    ("import devforge.pipeline.phases.demand",
     "import codegen.application.phases.requirements_discussion"),
    ("import devforge.pipeline.phases.document",
     "import codegen.application.phases.documentation"),
    ("import devforge.pipeline.phases.",
     "import codegen.application.phases."),
    ("from devforge.pipeline.phases",
     "from codegen.application.phases"),
    ("import devforge.pipeline.phases",
     "import codegen.application.phases"),
    ("from devforge.pipeline.patterns", "from codegen.application.patterns"),
    ("from devforge.pipeline.pipeline", "from codegen.application.pipeline"),
    ("from devforge.pipeline.chain", "from codegen.application.chat_chain"),
    ("import devforge.pipeline.patterns", "import codegen.application.patterns"),
    ("import devforge.pipeline.pipeline", "import codegen.application.pipeline"),
    ("import devforge.pipeline.chain", "import codegen.application.chat_chain"),
    ("devforge.pipeline.patterns", "codegen.application.patterns"),
    ("devforge.pipeline.phases.", "codegen.application.phases."),
    ("devforge.pipeline.pipeline", "codegen.application.pipeline"),
    ("devforge.pipeline.chain", "codegen.application.chat_chain"),
    # Phase 6 — serving 域（specific 在前）
    ("from devforge.server.connection", "from serving.application.ws_manager"),
    ("import devforge.server.connection", "import serving.application.ws_manager"),
    ("from devforge.server.runner", "from serving.application.run_queue"),
    ("import devforge.server.runner", "import serving.application.run_queue"),
    ("from devforge.server.routes", "from serving.interfaces.routes"),
    ("import devforge.server.routes", "import serving.interfaces.routes"),
    ("from devforge.server.app", "from serving.interfaces.app"),
    ("import devforge.server.app", "import serving.interfaces.app"),
    ("devforge.server.connection", "serving.application.ws_manager"),
    ("devforge.server.runner", "serving.application.run_queue"),
    ("devforge.server.routes", "serving.interfaces.routes"),
    ("devforge.server.app", "serving.interfaces.app"),
]

SCAN_ROOTS = ["devforge", "src", "tests", "benchmarks", "scripts"]


def collect_python_files() -> list[Path]:
    files: list[Path] = []
    for root_name in SCAN_ROOTS:
        root = PROJECT_ROOT / root_name
        if root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    # 本脚本的映射表本身含旧路径字符串 —— 排除自身防自改
    self_path = Path(__file__).resolve()
    return [p for p in files if p.resolve() != self_path]


def rewrite_file(path: Path, apply: bool) -> list[str]:
    text = path.read_text(encoding="utf-8")
    hits = []
    for old, new in REWRITE_RULES:
        if old in text:
            hits.append(f"    {old!r} -> {new!r}  x{text.count(old)}")
            text = text.replace(old, new)
    if hits and apply:
        path.write_text(text, encoding="utf-8")
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="实际写入文件（默认只报告）")
    args = parser.parse_args()

    changed = 0
    for path in collect_python_files():
        hits = rewrite_file(path, args.apply)
        if hits:
            changed += 1
            print(f"{'WROTE' if args.apply else 'WOULD CHANGE'} {path.relative_to(PROJECT_ROOT)}")
            print("\n".join(hits))
    print(f"\n{'已改写' if args.apply else '将改写'} {changed} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
