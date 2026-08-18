"""ChatChain — pipeline orchestrator: setup → execute."""
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from core.config import _project_root
from codegen.domain.blackboard import Blackboard
from codegen.domain.exceptions import ChatChainError

_OUT_DIR = _project_root() / "WareHouse"

# 运行工件目录（隐藏）：checkpoint / run_events / task.txt 不污染交付物。
# 交付目录只留代码、测试、文档、requirements。
ARTIFACT_DIR = ".devforge"

def artifact_path(directory: str, name: str) -> str:
    """工件路径：优先 .devforge/，兼容旧布局（工件在根目录时读取旧路径）。"""
    new = os.path.join(directory, ARTIFACT_DIR, name)
    if os.path.exists(new):
        return new
    old = os.path.join(directory, name)
    return old if os.path.exists(old) else new

class ChatChain:
    """Pipeline orchestrator — setup → execute."""

    def __init__(self, config: dict | None = None, task_prompt: str | None = None,
                 run_id: str = "", start_from: str = "",
                 project_dir: str = "", memory_dir: str = "") -> None:
        self.config = config or {}
        self.task_prompt_raw = task_prompt
        self.start_from = start_from
        self.run_id = run_id

        self.phases = self.config.get("pipeline", [])
        self.blackboard = Blackboard()
        self.blackboard["_run_id"] = run_id
        self.blackboard['task_prompt'] = self.task_prompt_raw
        self.blackboard['_phases'] = list(self.phases)
        # 隔离记忆库（benchmark 评测不污染生产记忆；空 = 全局库）
        self.blackboard["_memory_dir"] = memory_dir

        self.project_name = self._sanitise_name(task_prompt)
        self.start_time = time.strftime("%Y%m%d_%H%M%S")
        _OUT_DIR.mkdir(exist_ok=True)

        # iterate.json 单独跑（pipeline=["Iterate"]）且未指定 project →
        # 自动定位最近一次同任务的交付目录；无历史交付则明确报错
        #（此前拿到空 blackboard 凭空"修改"一个不存在的项目）
        if not project_dir and not start_from and self.phases == ["Iterate"]:
            project_dir = self._resolve_iterate_target()

        out_dir = self._resolve_output_dir(project_dir, start_from)
        self._restore_checkpoint(out_dir, project_dir, start_from)
        self._write_task_files(out_dir, task_prompt, project_dir)
        self._init_runtime(out_dir, start_from, project_dir)

        import codegen.application.phases  # noqa: F401

    # ── 装配子步骤（二轮拆分：__init__ 88 行 → 编排 + 子步骤）──

    def _resolve_iterate_target(self) -> str:
        """iterate.json 单独跑（无 project 参数）→ 自动定位最近一次同任务
        交付目录；无历史交付则报错（此前拿到空 blackboard 凭空修改）。"""
        out_dir = self._find_existing_dir()
        if not out_dir:
            raise ChatChainError(
                "Iterate 流水线需要指定 project —— WareHouse 中未找到该任务"
                "的历史交付（请通过项目页的'迭代'按钮发起，或 /api/run 传 "
                "project= 参数）")
        return str(out_dir)

    def _resolve_output_dir(self, project_dir: str,
                            start_from: str) -> Path:
        """决定运行目录：迭代复用已有目录 / 重跑找历史目录 / 新建。"""
        if project_dir:
            # 增量迭代：在已有项目目录上跑（不新建、不删除），
            # 迭代 run 的工件复用同一 .devforge/
            out_dir = Path(project_dir)
            if not out_dir.is_dir():
                raise ChatChainError(f"Project dir not found: {project_dir}")
            return out_dir
        if start_from:
            # Find existing project directory from previous run
            out_dir = self._find_existing_dir()
            if not out_dir:
                raise ChatChainError("No previous run found to restart from "
                                     f"(phase '{start_from}' requires checkpoint)")
            return out_dir
        # append the run id — the timestamp has second granularity,
        # so two runs in the same second would collide (and the second
        # rmtree would delete the first run's output).
        suffix = f"_{self.run_id}" if self.run_id else ""
        out_dir = _OUT_DIR / (
            f"{self.project_name}_DevForge_{self.start_time}{suffix}")
        if out_dir.exists():
            import shutil
            shutil.rmtree(out_dir)
        out_dir.mkdir()
        return out_dir

    def _restore_checkpoint(self, out_dir: Path, project_dir: str,
                            start_from: str) -> None:
        """迭代：最近一次完整运行的 checkpoint；重跑：目标阶段前一个。"""
        if project_dir:
            # 迭代模式：加载最近一次完整运行的 checkpoint（requirements/modules）
            for prev in reversed(self._full_phases()):
                if self._load_checkpoint_or_warn(out_dir, prev, silent=True):
                    return
            return
        if start_from:
            # Load checkpoint from the phase BEFORE the target
            idx = self.phases.index(start_from) if start_from in self.phases else -1
            if idx > 0:
                self._load_checkpoint_or_warn(out_dir, self.phases[idx - 1])

    def _load_checkpoint_or_warn(self, out_dir: Path, phase_name: str,
                                 *, silent: bool = False) -> bool:
        ckpt = Path(artifact_path(str(out_dir), f"checkpoint_{phase_name}.json"))
        if ckpt.exists():
            self.blackboard.load_checkpoint(str(ckpt))
            return True
        if not silent:
            # 静默恢复会让重跑阶段拿不到 requirements/modules，
            # 覆盖校验空转、设计缺需求 —— 显式警告
            print(f"  [ChatChain] WARNING: checkpoint {ckpt.name} "
                  "missing — blackboard state will be incomplete", flush=True)
        return False

    def _write_task_files(self, out_dir: Path, task_prompt: str | None,
                          project_dir: str) -> None:
        self.blackboard['directory'] = str(out_dir)
        # 运行工件收进 .devforge/（checkpoint/run_events/task.txt 不混入交付物）
        (out_dir / ARTIFACT_DIR).mkdir(exist_ok=True)
        # Save task description for checkpoint recovery display。
        # 迭代模式不覆盖原 task.txt（保留初始任务描述），反馈单独存。
        if task_prompt and not project_dir:
            (out_dir / ARTIFACT_DIR / "task.txt").write_text(
                task_prompt, encoding="utf-8")
        elif task_prompt and project_dir:
            (out_dir / ARTIFACT_DIR / "feedback.txt").write_text(
                task_prompt, encoding="utf-8")

    def _init_runtime(self, out_dir: Path, start_from: str,
                      project_dir: str) -> None:
        from codegen.infrastructure.tools.registry import init
        # 执行环境自包含：不再为项目创建 venv。
        # - docker 模式：代码/测试跑在容器（容器内自动装依赖），项目
        #   venv 无用（交付物不带 35MB .venv）
        # - 宿主机模式：直接用 DevForge 根环境（sys.executable，自带
        #   pytest）—— 零安装、零网络、零失败面（不再依赖现场建
        #   venv + pip 装 pytest）
        init(project_dir=str(out_dir), venv_dir="")

    @staticmethod
    def _sanitise_name(task: str | None) -> str:
        return re.sub(r'[^\w\s-]', '', (task or "Project")[:30]).strip() or "Project"

    @staticmethod
    def _full_phases() -> list[str]:
        """完整流水线阶段清单（迭代模式用它找最近一次完整运行的 checkpoint）。"""
        try:
            from core.config import load_pipeline_config
            return load_pipeline_config().get("pipeline", [])
        except Exception:
            # 与 configs/default.json 的 pipeline 顺序保持一致（质检后文档）
            return ["RequirementsDiscussion", "Design", "Coding",
                    "Verification", "QualityGate", "Documentation"]

    def _find_existing_dir(self) -> Path | None:
        """Find the most recent project directory for the current task."""
        prefix = f"{self.project_name}_DevForge_"
        dirs = sorted(
            [d for d in _OUT_DIR.iterdir()
             if d.is_dir() and d.name.startswith(prefix)],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for d in dirs:
            if Path(artifact_path(str(d), "checkpoint.json")).exists():
                return d
        return None

    def run(self) -> str:
        """Run all phases (or from start_from), return project directory."""
        from codegen.application.pipeline import Pipeline
        from core.context import set_current_run

        set_current_run(self.blackboard["_run_id"])
        Pipeline(self.blackboard).run(self.phases, self.start_from)
        return self.blackboard["directory"]
