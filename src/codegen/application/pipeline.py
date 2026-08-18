"""Pipeline — orchestrate all phases in order."""
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from core.config import load_pipeline_config
from core.events import Events, HookRegistry
from codegen.application.chat_chain import ARTIFACT_DIR
from codegen.domain.exceptions import PipelineError
from codegen.domain.registry import PhaseRegistry

_log = logging.getLogger(__name__)

# 记忆写入串行锁：后台线程 + 阶段边界并发写 ChromaDB 需要互斥
_memory_lock = threading.Lock()

class Pipeline:
    """Run phases on a blackboard."""

    # QualityGate FAIL → jump back to Verification, at most this many times.
    # 可配置：configs/default.json quality_gate_max_loops
    MAX_QUALITY_GATE_LOOPS = 3

    def __init__(self, blackboard, *, feedback_drainer=None):
        self.blackboard = blackboard
        # DI：运行中反馈队列的消费端可注入（FeedbackPort）。
        # 默认用 serving 的 ws 反馈队列；测试/替换实现时传入。
        self._drain_feedback = feedback_drainer
        self._token_warned = False
        self._qg_loops = 0     # QualityGate FAIL 回跳次数（实例状态：_run_one 内更新）
        self._qg_max = int(load_pipeline_config().get(
            "quality_gate_max_loops", self.MAX_QUALITY_GATE_LOOPS))
        self._memory_threads: list[threading.Thread] = []   # 后台记忆写入

    def run(self, phases: list[str], start_from: str = ""):
        HookRegistry.trigger(Events.PIPELINE_START, phases=phases)

        if start_from and start_from in phases:
            phases = phases[phases.index(start_from):]

        retries_cfg = load_pipeline_config().get("phase_retries", {})
        i = 0
        try:
            while i < len(phases):
                i = self._run_one(phases, i, retries_cfg)
        finally:
            # 无论成败都把本轮产物入 git（失败版本也可回滚对比）
            self._git_commit()
            # 记忆写入是 daemon 线程 —— 不 join 会在进程退出时丢最后一条
            for t in self._memory_threads:
                t.join(timeout=15)

    def _run_one(self, phases, i, retries_cfg):
        name = phases[i]
        cls = PhaseRegistry.get(name)
        phase = cls(self.blackboard)
        started = time.time()
        max_retries = int(retries_cfg.get(name, 0) or 0)

        HookRegistry.trigger(Events.PHASE_START, phase=name,
                             index=i, total=len(phases))
        try:
            phase.run()
            HookRegistry.trigger(Events.PHASE_END, phase=name,
                                 elapsed=round(time.time() - started, 1),
                                 tokens=self._phase_tokens(),
                                 index=i, total=len(phases))
            self._save_checkpoint(name)
        except Exception as exc:
            elapsed = round(time.time() - started, 1)
            HookRegistry.trigger(Events.PHASE_ERROR, phase=name,
                                 error=str(exc), elapsed=elapsed)
            if max_retries > 0:
                print(f"  [Pipeline] {name} failed ({exc}) — retrying "
                      f"({max_retries} left)", flush=True)
                HookRegistry.trigger(
                    Events.PHASE_RETRY, phase=name,
                    reason="error", loop=1)
                retries_cfg[name] = max_retries - 1
                return i
            raise

        # 运行中追加需求：阶段边界消费用户消息。已编码过（目录有 .py）→
        # 走增量迭代（Iterate）；尚未编码 → 回退 Design 重新设计
        target = self._check_user_feedback(phases)
        if target is not None:
            HookRegistry.trigger(Events.PHASE_RETRY, phase=target[0],
                                 reason="feedback", loop=1)
            return target[1]
        self._check_token_budget()

        # QualityGate loop: FAIL 或 WARN 含未达标项 → redo Verification
        if name == "QualityGate":
            qg = self.blackboard.get("quality_gate", {})
            verdict = qg.get("verdict", "")
            # 回跳条件：功能项 NO/PARTIAL（inspector 判定）→ 重修。
            # 证据门槛追加项（source="evidence"，测试失败/覆盖率低）不触发
            # 回跳 —— 修复者修不了测试框架问题，回跳只会白烧验证轮次
            missing = [f for f in (qg.get("features") or [])
                       if isinstance(f, dict)
                       and f.get("status") in ("NO", "PARTIAL")
                       and f.get("source") != "evidence"]
            self.blackboard["quality_gate_loops"] = self._qg_loops
            if (verdict in ("FAIL", "WARN") and missing
                    and self._qg_loops < self._qg_max):
                self._qg_loops += 1
                self.blackboard["quality_gate_loops"] = self._qg_loops
                HookRegistry.trigger(
                    Events.PHASE_RETRY, phase="Verification",
                    reason="fail", loop=self._qg_loops)
                try:
                    # a custom pipeline may omit Verification — no
                    # jump target, so stop instead of raising ValueError.
                    return phases.index("Verification")
                except ValueError:
                    return len(phases)
            if verdict in ("FAIL", "WARN") and missing:
                # Loops exhausted — deliver with an explicit failure flag
                #（项目仍交付，只是带缺陷标记 —— 历史页可见可重跑）
                self.blackboard["quality_gate_failed"] = True
                print("  [Pipeline] QualityGate "
                      f"{verdict}（{len(missing)} 项未达标）after "
                      f"{self._qg_max} retries — delivering with failure flag",
                      flush=True)
                # 未通过质检 → 清掉本 run 已写入的记忆（失败经验不入库）
                directory = self.blackboard.get("directory", "")
                if directory:
                    try:
                        from memory.infrastructure.chroma_store import MemoryStore
                        project = (Path(directory).name
                                   .split("_DevForge_", 1)[0])[:80]
                        if project:
                            MemoryStore(
                                chroma_dir=self.blackboard.get("_memory_dir", "")
                            ).delete_project(project)
                            print(f"  [Memory] 质检未通过 — 已清除 {project} 的记忆",
                                  flush=True)
                    except Exception:
                        _log.warning("Failed to delete memory for failed "
                                     "project %s", directory)
        return i + 1

    # ── 运行中追加需求 / 预算 / git ────────────────────

    def _check_user_feedback(self, phases: list[str]) -> tuple[str, int] | None:
        """消费用户消息队列；有消息 → 记录需求历史并返回回退目标。

        已编码过（项目目录有 .py 文件）→ 增量迭代（Iterate，改动最小化）；
        尚未编码 → 回退 Design 重新设计。返回 (phase 名, phases 下标)。
        """
        from serving.application.ws_manager import drain_feedback as _default_drainer
        drainer = self._drain_feedback or _default_drainer
        run_id = self.blackboard.get("_run_id", "")
        feedback = drainer(run_id)
        if not feedback:
            return None
        history = list(self.blackboard.get("requirements_history", []) or [])
        history.append({
            "timestamp": time.time(),
            "feedback": list(feedback),
            "requirements": dict(self.blackboard.get("requirements", {}) or {}),
        })
        self.blackboard["requirements_history"] = history
        self.blackboard["user_feedback"] = feedback

        # 已编码过 → 增量迭代；否则回退 Design。
        # isdir 防御：目录可能未建/已被删（陈旧 checkpoint），
        # listdir 会抛 FileNotFoundError 把已完成阶段变成 run 级失败
        directory = self.blackboard.get("directory", "")
        has_code = bool(directory) and os.path.isdir(directory) and any(
            f.endswith(".py") and not f.startswith("test_")
            for f in os.listdir(directory))
        if has_code:
            try:
                return "Iterate", phases.index("Iterate")
            except ValueError:
                print("  [Pipeline] 追加需求需要增量迭代但 pipeline 无 "
                      "Iterate 阶段 — 回退 Design", flush=True)
        print(f"  [Pipeline] 用户追加需求: {feedback[:1]} — "
              f"回退 {'Iterate（增量修改）' if has_code else 'Design'}",
              flush=True)
        try:
            return "Design", phases.index("Design")
        except ValueError:
            return None

    def _check_token_budget(self):
        """超出 token 预算 → 警告事件；超 token_budget_stop（>0 时）→
        终止运行（成本硬护栏，默认 0 = 不拦截）。"""
        cfg = load_pipeline_config().get("llm", {})
        budget = int(cfg.get("token_budget", 0) or 0)
        if not budget:
            return
        used = self._phase_tokens().get("prompt_tokens", 0)
        stop = int(cfg.get("token_budget_stop", 0) or 0)
        if stop and used > stop:
            raise PipelineError(
                f"token 消耗 {used} 超过硬上限 {stop} — 运行终止（可调 "
                "configs/default.json llm.token_budget_stop）")
        if used > budget and not self._token_warned:
            self._token_warned = True
            HookRegistry.trigger("token_warning", used=used, budget=budget)

    def _git_commit(self):
        """生成物入 git：每轮运行一个 commit（可回滚/对比）。失败静默。"""
        directory = self.blackboard.get("directory", "")
        if not directory or not os.path.isdir(directory):
            return
        try:
            gitignore = os.path.join(directory, ".gitignore")
            if not os.path.exists(gitignore):
                with open(gitignore, "w", encoding="utf-8") as f:
                    f.write(".venv/\n__pycache__/\n.pytest_cache/\n"
                            ".coverage\n.devforge/\n")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=directory,
                           capture_output=True, timeout=30)
            subprocess.run(["git", "add", "-A"], cwd=directory,
                           capture_output=True, timeout=30)
            subprocess.run(
                ["git", "-c", "user.name=DevForge",
                 "-c", "user.email=devforge@local", "commit", "-q", "-m",
                 (self.blackboard.get("task_prompt") or "DevForge run")[:100]],
                cwd=directory, capture_output=True, timeout=60)
        except Exception:
            _log.warning("git commit failed for %s", directory)

    # ── Usage ───────────────────────────────────────────

    def _phase_tokens(self) -> dict:
        """Aggregate per-agent token usage so far (for the frontend)."""
        log = self.blackboard.get("usage_log", {})
        prompt = sum(e.get("prompt_tokens", 0) for e in log.values())
        completion = sum(e.get("completion_tokens", 0) for e in log.values())
        calls = sum(e.get("calls", 0) for e in log.values())
        return {"prompt_tokens": prompt, "completion_tokens": completion,
                "calls": calls}

    # ── Checkpoint ──────────────────────────────────────

    def _save_checkpoint(self, phase_name: str):
        """Persist blackboard state after a phase completes."""
        # 迭代阶段不覆盖完整运行的 checkpoint（resume 提示仍指向上次
        # 完整运行的阶段），迭代改动由 git commit 记录
        if phase_name == "Iterate":
            return
        directory = self.blackboard.get("directory", "")
        if not directory:
            return
        os.makedirs(directory, exist_ok=True)
        os.makedirs(os.path.join(directory, ARTIFACT_DIR), exist_ok=True)
        path = os.path.join(directory, ARTIFACT_DIR, "checkpoint.json")
        data = {"phase": phase_name}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                _log.warning("Failed to read checkpoint in %s", directory)
        data["phase"] = phase_name
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.blackboard.save_checkpoint(
            os.path.join(directory, ARTIFACT_DIR,
                         f"checkpoint_{phase_name}.json"))

        # Memory: persist phase output for cross-project retrieval。
        # 异步（后台线程）—— ChromaDB upsert 较慢，同步做会卡住阶段边界
        #（用户观察：记忆更新完才继续下一步）。串行锁防并发写。
        # 质检记忆门槛：仅 PASS 写入（WARN/FAIL 是"未达标经验"，会被
        # _completed_projects 当成完成项目召回，污染后续项目）
        qg = self.blackboard.get("quality_gate", {}) or {}
        if phase_name == "QualityGate" and qg.get("verdict") != "PASS":
            return

        def _write_memory():
            with _memory_lock:
                try:
                    from memory.infrastructure.chroma_store import MemoryStore
                    # 稳定项目标识：目录名的任务前缀（"..._DevForge_时间戳_runid"）。
                    # project_name 来自 PM 输出（同任务各 run 可能不同）会导致
                    # 同函数每次 run 写成新条目，旧版本永久堆积并污染召回 ——
                    # 任务前缀让同任务的重跑 upsert 覆盖旧记忆，只保留最新实现。
                    project = (Path(directory).name.split("_DevForge_", 1)[0]
                               or self.blackboard.get("requirements", {})
                               .get("project_name", ""))[:80]
                    # blackboard 可指定隔离记忆库（benchmark 不污染生产记忆）
                    MemoryStore(
                        chroma_dir=self.blackboard.get("_memory_dir", "")
                    ).write_phase(project, phase_name, self.blackboard)
                except Exception:
                    _log.warning("Failed to write memory for %s", phase_name)

        t = threading.Thread(target=_write_memory, daemon=True)
        self._memory_threads.append(t)
        t.start()

