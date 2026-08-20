"""Pipeline — orchestrate all phases in order (O6 spec-driven engine).

控制流（错误重试、token 预算、质检回跳/升级目标与条件）统一读
``PipelineSpec``（codegen/application/spec.py，来自 pipeline_spec 配置段），
engine 不再硬编码跳转目标；升级条件用内置谓词枚举，不用 eval。
横切关注点（checkpoint/事件/预算/反馈/历史归档）是 engine 级钩子方法。
"""
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
from codegen.application.spec import (
    ESCALATE_CONDITION_SAME_GAPS,
    PipelineSpec,
)
from codegen.domain.exceptions import PipelineError
from codegen.domain.registry import PhaseRegistry

_log = logging.getLogger(__name__)

# 记忆写入串行锁：后台线程 + 阶段边界并发写 ChromaDB 需要互斥
_memory_lock = threading.Lock()

class Pipeline:
    """Run phases on a blackboard (spec-driven)."""

    # QualityGate FAIL → jump back to Verification, at most this many times.
    # 默认值；实际由 spec.quality_gate.max_loops 决定
    #（configs/default.json pipeline_spec.quality_gate.max_loops）
    MAX_QUALITY_GATE_LOOPS = 3

    def __init__(self, blackboard, *, feedback_drainer=None):
        self.blackboard = blackboard
        # DI：运行中反馈队列的消费端可注入（FeedbackPort）。
        # 默认用 serving 的 ws 反馈队列；测试/替换实现时传入。
        self._drain_feedback = feedback_drainer
        self._token_warned = False
        self._qg_loops = 0     # QualityGate FAIL 回跳次数（实例状态）
        self._spec = PipelineSpec.from_config(load_pipeline_config())
        self._retries_left: dict[str, int] = {}   # 各阶段剩余重试次数
        self._memory_threads: list[threading.Thread] = []   # 后台记忆写入

    def run(self, phases: list[str], start_from: str = ""):
        HookRegistry.trigger(Events.PIPELINE_START, phases=phases)

        if start_from and start_from in phases:
            phases = phases[phases.index(start_from):]

        i = 0
        try:
            while i < len(phases):
                i = self._run_one(phases, i)
        finally:
            # 无论成败都把本轮产物入 git（失败版本也可回滚对比）
            self._git_commit()
            # 记忆写入是 daemon 线程 —— 不 join 会在进程退出时丢最后一条
            for t in self._memory_threads:
                t.join(timeout=15)

    def _run_one(self, phases, i):
        name = phases[i]
        cls = PhaseRegistry.get(name)
        phase = cls(self.blackboard)
        started = time.time()
        # O15 第一步：阶段起点 token 快照（只统计不降级，先看数据再调权重）
        self._budget_start = self._phase_tokens().get("prompt_tokens", 0)
        ps = self._spec.get(name)
        max_retries = ps.retry_on_error if ps else 0
        # setdefault 而非赋值：重跑（retry 分支 return i 后）不能把
        # 剩余次数重置回满，否则耗尽场景无限重试
        self._retries_left.setdefault(name, max_retries)

        HookRegistry.trigger(Events.PHASE_START, phase=name,
                             index=i, total=len(phases))
        # 阶段预算下发给 blackboard：阶段实现（Verification 修复轮）在
        # 循环边界检查 _phase_over_budget() 提前收尾，不再只发统计事件
        self.blackboard["_phase_budget"] = ps.budget if ps else 0
        self.blackboard["_phase_budget_start"] = self._budget_start
        try:
            phase.run()
            self._on_phase_success(name, phase, started, i, len(phases))
        except Exception as exc:
            self._on_phase_error(name, phase, exc, started, i, len(phases))
            if self._retries_left.get(name, 0) > 0:
                print(f"  [Pipeline] {name} failed ({exc}) — retrying "
                      f"({self._retries_left[name]} left)", flush=True)
                HookRegistry.trigger(
                    Events.PHASE_RETRY, phase=name,
                    reason="error", loop=1)
                self._retries_left[name] -= 1
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

        # QualityGate loop：回跳/升级决策由 spec 驱动（_quality_gate_decision）
        if name == "QualityGate":
            decision = self._quality_gate_decision(phases)
            if decision is not None:
                return decision
        return i + 1

    # ── 阶段生命周期钩子（O6 横切关注点）─────────────────

    def _on_phase_success(self, name, phase, started, i, total):
        """阶段成功：事件 + checkpoint + 事件落盘 + 预算统计 + 历史归档。"""
        HookRegistry.trigger(Events.PHASE_END, phase=name,
                             elapsed=round(time.time() - started, 1),
                             tokens=self._phase_tokens(),
                             index=i, total=total)
        self._save_checkpoint(name)
        self._persist_events()
        self._emit_phase_usage(name)
        self._archive_agent_history(name, phase)

    def _on_phase_error(self, name, phase, exc, started, i, total):
        """阶段失败：事件 + 增量落盘 + 预算统计 + 历史归档（部分）。"""
        elapsed = round(time.time() - started, 1)
        HookRegistry.trigger(Events.PHASE_ERROR, phase=name,
                             error=str(exc), elapsed=elapsed)
        # 阶段失败也增量落盘（崩溃点之前的事件保留）
        self._persist_events()
        self._emit_phase_usage(name)
        # O10: 失败也归档（部分历史）—— retry 重跑时 agent 恢复上下文
        self._archive_agent_history(name, phase)

    def _quality_gate_decision(self, phases) -> int | None:
        """质检回跳/升级决策（O6 spec 驱动，纯方法便于测试）。

        FAIL/WARN 含未达标项（非 evidence）→ 回跳 spec.quality_gate.fail_jump
        （默认 Verification）；同缺口第二次 QG 且满足升级条件谓词 → 跳
        escalate_jump（默认 Design，重新设计），并把质检反馈喂给 CTO；
        轮次耗尽 → 打失败标记交付（带缺陷标记，不清零）。
        返回跳转下标（None = 继续下一步）。
        """
        qg = self.blackboard.get("quality_gate", {})
        verdict = qg.get("verdict", "")
        # 回跳条件：功能项 NO/PARTIAL（inspector 判定）→ 重修。
        # 证据门槛追加项（source="evidence"，测试失败/覆盖率低）不触发
        # 回跳 —— 修复者修不了测试框架问题，回跳只会白烧验证轮次
        missing = [f for f in (qg.get("features") or [])
                   if isinstance(f, dict)
                   and f.get("status") in ("NO", "PARTIAL")
                   and f.get("source") != "evidence"]
        qs = self._spec.quality_gate
        self.blackboard["quality_gate_loops"] = self._qg_loops
        if verdict in ("FAIL", "WARN") and missing \
                and self._qg_loops < qs.max_loops:
            names = sorted(f.get("name", "") for f in missing)
            prev = self.blackboard.get("qg_missing_names") or []
            # 第 2 次 QG 且缺口完全没变 → 升级（fixer 已尽力但修不动 =
            # 架构/设计问题）。判断必须在自增前；目标阶段不在流水线时
            # 保持旧行为（继续回 fail_jump）。
            escalate = (
                qs.escalate_condition == ESCALATE_CONDITION_SAME_GAPS
                and self._qg_loops == 1 and bool(prev)
                and set(prev) == set(names)
                and qs.escalate_jump in phases)
            self._qg_loops += 1
            self.blackboard["quality_gate_loops"] = self._qg_loops
            self.blackboard["qg_missing_names"] = names
            target = qs.escalate_jump if escalate else qs.fail_jump
            if escalate:
                self.blackboard["qg_feedback"] = "\n".join(
                    f"- [{f.get('status')}] {f.get('name', '?')}: "
                    f"{f.get('notes', '')}" for f in missing)
            HookRegistry.trigger(
                Events.PHASE_RETRY, phase=target,
                reason="escalate" if escalate else "fail",
                loop=self._qg_loops)
            try:
                # a custom pipeline may omit the target — no
                # jump target, so stop instead of raising ValueError.
                return phases.index(target)
            except ValueError:
                return len(phases)
        if missing:
            self.blackboard["qg_missing_names"] = []
        if verdict in ("FAIL", "WARN") and missing:
            # Loops exhausted — deliver with an explicit failure flag
            #（项目仍交付，只是带缺陷标记 —— 历史页可见可重跑）
            self.blackboard["quality_gate_failed"] = True
            print("  [Pipeline] QualityGate "
                  f"{verdict}（{len(missing)} 项未达标）after "
                  f"{qs.max_loops} retries — delivering with failure flag",
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
        return None

    # ── 运行中追加需求 / 预算 / git ────────────────────

    def _persist_events(self):
        """阶段边界增量落盘 run_events.json —— 服务中途被杀时保留已完成
        阶段的运行记录（此前只在 run 完成/失败时写一次，崩溃即全丢，
        token 分布/阶段耗时无法事后分析）。失败静默。"""
        try:
            from serving.infrastructure.run_repository import persist_run
            persist_run(self.blackboard.get("_run_id", ""),
                        self.blackboard.get("task_prompt", ""))
        except Exception:
            _log.warning("Failed to persist run events")

    def _emit_phase_usage(self, phase_name: str):
        """阶段 token 消耗统计事件（O15 第一步：只统计不降级）。

        前端/分析据此看每个阶段烧多少 token、占预算百分比 ——
        权重调优依据。预算为 0/未配置的阶段不发事件。
        预算读 spec（pipeline_spec.phases.<name>.budget，回落 phase_budget）。
        """
        ps = self._spec.get(phase_name)
        budget = ps.budget if ps else 0
        if not budget:
            return
        consumed = self._phase_tokens().get("prompt_tokens", 0) - self._budget_start
        HookRegistry.trigger(
            "phase_usage", phase=phase_name, budget=budget,
            consumed=max(consumed, 0),
            pct=round(max(consumed, 0) * 100.0 / budget, 1))

    def _archive_agent_history(self, phase_name: str, phase):
        """O10: 阶段结束后把该阶段所有 agent 的对话历史归档到
        .devforge/agent_history/<phase>/<agent>.json —— 同阶段重跑
        （retry/回跳/断点恢复）据此恢复上下文。失败静默。"""
        agents = getattr(phase, "_created_agents", None) or []
        directory = self.blackboard.get("directory", "")
        if not directory or not agents:
            return
        base = os.path.join(directory, ARTIFACT_DIR, "agent_history", phase_name)
        try:
            os.makedirs(base, exist_ok=True)
            for agent in agents:
                try:
                    data = agent.serialize_history()
                    with open(os.path.join(base, f"{agent.name}.json"),
                              "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                except Exception:
                    _log.warning("Failed to archive history for %s", agent.name)
        except Exception:
            _log.warning("Failed to archive agent history for %s", phase_name)

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
            if "Iterate" in phases:
                return "Iterate", phases.index("Iterate")
            # 默认流水线没有 Iterate 阶段 —— 动态插入（插到 QualityGate
            # 之前，迭代后让质检重新验证）。此前静默回退 Design = 已编码
            # 项目追加需求被全量推倒重来，增量迭代特性形同虚设
            idx = phases.index("QualityGate") if "QualityGate" in phases \
                else len(phases)
            phases.insert(idx, "Iterate")
            print(f"  [Pipeline] 已动态插入 Iterate 阶段（位置 {idx}）",
                  flush=True)
            return "Iterate", idx
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
        qg = self.blackboard.get("quality_gate", {}) or {}
        if phase_name == "QualityGate" \
                and not self._quality_gate_memory_allowed(qg):
            return
        t = threading.Thread(
            target=self._write_memory, args=(directory, phase_name),
            daemon=True)
        self._memory_threads.append(t)
        t.start()

    # ── QualityGate 记忆门槛 ────────────────────────────

    def _quality_gate_memory_allowed(self, qg: dict) -> bool:
        """质检结果能否写入跨项目记忆（纯方法，便于测试）。

        - FAIL 一律不写（含平台契约 FAIL —— 此前只靠 score 间接拦截，
          平台 FAIL 高分会被当"差一点就过"写库，直接违背 README
          "FAIL 自动清除"的设计；score 归零是门禁侧兜底，这里显式拦截）
        - PASS 写入
        - WARN 仅当无证据失败（测试失败/覆盖率低）且得分达标 ——
          否则测试全挂的 WARN 会被 _completed_projects 当成完成项目
          召回污染后续项目；"差一点就过"的高分 WARN 其 verified 函数
          值得复用
        """
        verdict = qg.get("verdict", "")
        if verdict == "FAIL":
            return False
        if verdict != "PASS":
            features = qg.get("features") or []
            has_evidence_fail = any(
                isinstance(f, dict)
                and f.get("source") == "evidence"
                and f.get("status") in ("NO", "PARTIAL")
                for f in features)
            min_score = int(load_pipeline_config().get(
                "memory", {}).get("min_warn_completed_score", 80) or 80)
            if has_evidence_fail or int(qg.get("score", 0) or 0) < min_score:
                return False
        return True

    def _write_memory(self, directory: str, phase_name: str):
        """后台线程写跨项目记忆（ChromaDB upsert 慢，同步会卡住阶段边界）。"""
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

