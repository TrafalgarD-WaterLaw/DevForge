"""serving 域异常 —— 继承项目基类 BaseAppError（/7 异常体系）。"""
from core.exceptions import BaseAppError

class RunNotFoundError(BaseAppError):
    """run_id 不存在（或已过期被清理）。"""

class IllegalRunStateTransitionError(BaseAppError):
    """状态机非法迁移（如 queued 直接 complete）。"""

    def __init__(self, run_id: str, current: str, target: str) -> None:
        super().__init__(f"run {run_id}: illegal transition {current} -> {target}")
        self.run_id = run_id
        self.current = current
        self.target = target
