"""RunState 实体 —— 任务运行状态机。

当前存储层（run_repository）用 dict + 本文件的 RunStatus/迁移规则做校验；
RunState 实体作为文档化的领域模型，后可将 dict 升级为实体。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from serving.domain.exceptions import IllegalRunStateTransitionError

class RunStatus(str, Enum):
    """运行状态。合法迁移：STARTING→RUNNING→(COMPLETE|ERROR)；STARTING→QUEUED→STARTING。"""

    STARTING = "starting"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"

@dataclass
class RunState:
    """一次流水线运行的聚合实体。业务规则（状态迁移）内聚在此，不散落 Service。"""

    run_id: str
    task_prompt: str
    status: RunStatus = RunStatus.STARTING
    started_at: float = 0.0
    project_dir: str | None = None
    error_message: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    _sequence: int = 0

    def enqueue(self, now: float) -> None:
        self._require_transition_from(RunStatus.STARTING, RunStatus.QUEUED)
        self.status = RunStatus.QUEUED

    def mark_running(self) -> None:
        if self.status not in (RunStatus.STARTING, RunStatus.QUEUED):
            raise IllegalRunStateTransitionError(
                self.run_id, self.status.value, RunStatus.RUNNING.value)
        self.status = RunStatus.RUNNING

    def complete(self, project_dir: str) -> None:
        self._require_transition_from(RunStatus.RUNNING, RunStatus.COMPLETE)
        self.status = RunStatus.COMPLETE
        self.project_dir = project_dir

    def fail(self, error_message: str) -> None:
        if self.status not in (RunStatus.RUNNING, RunStatus.STARTING,
                               RunStatus.QUEUED):
            raise IllegalRunStateTransitionError(
                self.run_id, self.status.value, RunStatus.ERROR.value)
        self.status = RunStatus.ERROR
        self.error_message = error_message

    def next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence

    def _require_transition_from(self, expected: RunStatus,
                                 target: RunStatus) -> None:
        if self.status is not expected:
            raise IllegalRunStateTransitionError(
                self.run_id, self.status.value, target.value)
