"""模块契约值对象 —— 从 blackboard.py 抽取。"""

from dataclasses import dataclass

@dataclass
class Contract:
    """API contract for a module — the single source of truth."""
    module: str
    version: int
    exports: list         # [{name, signature, doc}]
    dependencies: list    # [module_name]
    updated_at: float = 0.0
    updated_by: str = ""
