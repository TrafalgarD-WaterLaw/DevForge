"""Verification — multi-lens review + test + fix in one cycle."""

import difflib
import json
import logging
import os
from core.config import load_phases_config, load_pipeline_config
from core.events import Events, HookRegistry
from codegen.application.patterns import parallel
from codegen.application.process import run_process, trim_paths
from codegen.domain.phase import Phase
from codegen.domain.registry import register_phase
from codegen.domain.validate import validate_output, validated_react

_log = logging.getLogger(__name__)

def run_project_tests(
    directory: str, run_process, entry_point: str = ""
) -> tuple[bool, str, bool]:
    """Run pytest（或入口）on *directory* — 验证与质检共用。

    Returns (has_bugs, output, infra_failed)。
    *infra_failed* = 测试基础设施不可用（pytest 装不上且入口也跑不通）——
    测试没跑起来 ≠ 项目 bug，调用方不能据此判 FAIL 或进修复循环。
    *run_process* 是执行函数 (cmd, cwd, timeout) →
    (stdout_bytes, stderr_bytes, returncode)。
    """
    from codegen.infrastructure.tools.registry import (
        cov_args,
        docker_script,
        ensure_pytest,
        runtime,
    )

    python = runtime().venv_python()
    cwd = os.path.normpath(directory)
    test_files = sorted(
        (
            f
            for f in os.listdir(directory)
            if f.startswith("test_") and f.endswith(".py")
        )
    )
    if test_files:
        # docker 沙箱：与 tester 工具同一条链路（docker_script 统一
        # unset 代理 + pip 缓存卷），容器内自动补装 pytest —— 之前用
        # 裸 sandbox_prefix 跑 pytest 导致"No module named pytest"
        from codegen.infrastructure.tools.registry import docker_pytest_script
        cmd = docker_script(directory, docker_pytest_script(test_files))
        if cmd:
            out, err, code = run_process(cmd, cwd, timeout=120)
            output = trim_paths((err + out).decode("utf-8", errors="replace"),
                                directory)
            if code == 0:
                return (False, "All tests passed.", False)
            return (True, output, False)
        # 宿主机直跑：venv 必须装好 pytest
        if ensure_pytest(python):
            cmd = [
                python,
                "-m",
                "pytest",
                "-q",
                "--no-header",
                *cov_args(python, directory),
                *test_files,
            ]
            # 宿主机回退同样注入运行期沙箱 shim（T9-A）：文件操作逃逸
            # 项目根/系统临时目录会被拦截
            from codegen.infrastructure.sandbox import sandbox_env
            host_env = {**os.environ, **sandbox_env(directory)}
            out, err, code = run_process(cmd, cwd, timeout=120, env=host_env)
            output = trim_paths((err + out).decode("utf-8", errors="replace"),
                                directory)
            if code == 0:
                return (False, "All tests passed.", False)
            return (True, output, False)
        _log.warning("pytest unavailable in venv — falling back to entry")
    entry = entry_point or "main.py"
    if not os.path.exists(os.path.join(directory, entry)) and os.path.exists(
        os.path.join(directory, "cli.py")
    ):
        entry = "cli.py"
    out, err, code = run_process([python, entry], cwd, timeout=30)
    output = trim_paths((err + out).decode("utf-8", errors="replace"), directory)
    # 有测试文件但 pytest 装不上 → fallback 入口运行，结果不可信标记 infra
    infra_failed = bool(test_files)
    if code == 0:
        return (False, "The software ran successfully without errors.", infra_failed)
    if output and "Traceback" in output:
        return (True, output, infra_failed)
    return (bool(output), output or "Process exited with non-zero code.",
            infra_failed)

@register_phase
class Verification(Phase):
    """Multi-lens review + automated test + fix loop."""

    def _scan_project_files(self, directory: str) -> dict[str, str]:
        """扫描项目全部 .py 文件内容（磁盘权威，含测试文件）。"""
        files: dict[str, str] = {}
        skip = {".venv", "__pycache__", ".git", ".devforge", ".pytest_cache"}
        for root, dirs, fs in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in skip]
            for f in fs:
                if f.endswith(".py"):
                    p = os.path.join(root, f)
                    try:
                        files[os.path.relpath(p, directory)] = open(
                            p, encoding="utf-8", errors="replace").read()
                    except OSError:
                        pass
        return files

    def _changed_files(self, directory: str) -> list[str]:
        """fixer 修复前后源码 diff（磁盘权威，含测试文件 ——
        reload_codes 跳过 test_*.py，仅靠 blackboard.codes 会漏掉
        "只改了测试"的修复）。"""
        if not directory:
            return []
        pre = getattr(self, "_pre_files", None)
        if pre is None:
            return []
        cur = self._scan_project_files(directory)
        return sorted([f for f in cur if cur[f] != pre.get(f)]
                      + [f for f in pre if f not in cur])

    def run(self):
        directory = self.blackboard.get("directory", "")
        lenses = load_phases_config().get("Verification", {}).get("lenses", [])
        rounds = int(load_pipeline_config().get("verification_rounds", 2) or 2)
        for loop in range(1, rounds + 1):
            HookRegistry.trigger("review_round", phase="Verification", loop=loop)
            # 阶段预算：超预算停止修复循环，带当前状态进质检降级交付
            #（此前修复循环可烧到全局熔断才停，整任务陪葬）
            if self._phase_over_budget():
                print("  [Verification] 阶段预算耗尽 — 停止修复，"
                      "带当前状态进入质检", flush=True)
                break
            # 第二轮起增量复审：fixer 改了什么就只审什么（全新 reviewer
            # 重读全部文件是 token 大头）；fixer 没改任何文件 → 复审无意义
            if loop > 1:
                changed = self._changed_files(directory)
                if not changed:
                    print("  [Verification] fixer 未改动任何文件 — 跳过复审",
                          flush=True)
                    break
            else:
                changed = None
            # 修复前快照（磁盘）—— 下一轮增量复审据此 diff
            if directory:
                self._pre_files = self._scan_project_files(directory)
            self._pre_codes = dict(self.blackboard.codes)
            # 测试先行：reviewer 基于真实行为证据审查。此前测试输出只给
            # fixer，reviewer 拿不到 —— 审查与测试两条证据链脱节，审查员
            # 重复发现 tester 已报告的问题。同一份输出也喂给 fixer。
            has_bugs, test_output, infra_failed = self._run_tests()
            review_texts, discarded, valid = self._run_review_round(
                lenses, loop, test_output, changed=changed,
                tests_passed=(not has_bugs) and (not infra_failed))
            # 测试基础设施失败（pytest 装不上/入口也跑不通）≠ 项目 bug：
            # 没有 review 意见时不再为此单开 fix 轮（fixer 修不了环境问题）
            if (not has_bugs or infra_failed) \
                    and (not review_texts) and (not discarded):
                break
            self._run_fix_round(
                directory, review_texts, discarded,
                has_bugs and not infra_failed, test_output,
                loop=loop,
            )

    def _run_review_round(
        self, lenses: list, loop: int, test_output: str = "",
        changed: list[str] | None = None, tests_passed: bool = False,
    ) -> tuple[list[str], int, int]:
        """多 lens 并行审查 + schema 校验重试。返回
        (review_texts, discarded, valid)。

        *tests_passed*：测试通过时降噪 —— 审查范围收紧（只报测试覆盖
        不到的真实缺陷），且 fixer 队列只收 HIGH（实测：测试全过的代码
        被 reviewer 报 30+ 条 HIGH/MEDIUM/LOW，fixer 无从下手甚至
        零改动，修复轮空转烧 token）。
        """
        review_texts = []
        discarded = 0
        valid = 0
        requirements = json.dumps(
            self.blackboard.get("requirements", {}), ensure_ascii=False
        )
        contracts = self._contracts_text()
        # 测试通过 → 降噪指引（审查范围收窄，风格/性能/重构建议不报）
        if tests_passed:
            guidance = ("\n\n自动测试已全部通过，功能行为正确。请只报告"
                        "测试覆盖不到的真实缺陷（边界条件、错误处理、"
                        "安全漏洞、并发/资源问题）。不要报告风格、性能、"
                        "重构建议等非阻塞问题。")
        else:
            guidance = ""
        # 第二轮（fixer 修完后）是确认性重审：只读改动确认修复正确，
        # 不需要第一轮的全量深度分析 —— 限 3 轮（read_many + 输出），
        # 4 个 reviewer 两轮全量审查是 token 大头（6ff5ccee: 36 万）
        tasks = []
        for l in lenses:
            agent = self.agent("reviewer", tag=f"{l['name']}Reviewer")
            if loop > 1:
                self._cap_tool_rounds(agent, 3)
            if changed:
                # 增量复审：只注入变更文件内容 + 明确范围
                codes_text = "\n\n".join(
                    f"===== {f} =====\n{self.blackboard.codes.get(f, '')}"
                    for f in changed)
                focus = (f"本轮为修复后复审：仅审查以下变更文件，"
                         f"确认修复正确、未引入新问题。\n"
                         f"变更文件: {', '.join(changed)}\n\n"
                         f"Focus: {l['focus']}")
            else:
                codes_text = self.files
                focus = f"Focus: {l['focus']}"
            tasks.append(
                (
                    agent,
                    f"{focus}\n\n{self.prompt('reviewer', codes=codes_text, requirements=requirements, contracts=contracts, test_output=(test_output or '(no tests run)')[:1500])}{guidance}",
                    True,
                )
            )
        for agent, agent_output in parallel(tasks):
            if agent_output is not None and isinstance(agent_output, dict):
                errors = validate_output(agent_output, self.schema("reviewer"))
                if errors:
                    from core.config import load_sys_message
                    agent_output = validated_react(
                        agent,
                        load_sys_message("validate_retry",
                                         errors="; ".join(errors)),
                        self.schema("reviewer"),
                    )
                    if validate_output(agent_output, self.schema("reviewer")):
                        print(
                            f"  [{agent.name}] review output discarded (invalid)",
                            flush=True,
                        )
                        HookRegistry.trigger(
                            "review_discarded", agent=agent.name, loop=loop
                        )
                        discarded += 1
                        continue
            else:
                discarded += 1
                continue
            issues = agent_output.get("issues", [])
            valid += 1
            self.blackboard[f"review_{agent.name}"] = issues
            HookRegistry.trigger(
                "review_submitted", agent=agent.name, issues=issues, loop=loop
            )
            if issues:
                # 审查降噪：fixer 队列按 severity 过滤 —— 测试通过时
                # 只收 HIGH（行为正确，MEDIUM/LOW 多为风格/建议）；
                # 测试失败时收 HIGH+MEDIUM（LOW 一律不进 fixer 轮）。
                # 完整清单仍留在 blackboard（前端展示/审计）。
                floor = "HIGH" if tests_passed else "MEDIUM"
                keep_sev = {"HIGH", floor}
                kept = [i for i in issues
                        if i.get("severity", "LOW") in keep_sev]
                if not kept:
                    continue
                review_texts.append(
                    f"### {agent.name}\n"
                    + "\n".join(
                        (
                            f"- [{i['severity']}] {i['file']}:{i['line']} {i['description']}"
                            for i in kept
                        )
                    )
                )
        if review_texts:
            summary = f"审查完成: {len(review_texts)} 个审查者发现问题"
        elif discarded:
            summary = f"警告: {discarded} 份审查输出无效（结果存疑）"
        else:
            summary = "审查通过，无问题"
        HookRegistry.trigger(
            Events.CONVERSATION_TURN,
            agent="Verification",
            content=json.dumps({"message": summary}),
            turn=0,
        )
        self.blackboard["review_valid"] = valid
        self.blackboard["review_discarded"] = discarded
        return (review_texts, discarded, valid)

    def _run_fix_round(
        self,
        directory: str,
        review_texts: list[str],
        discarded: int,
        has_bugs: bool,
        test_output: str,
        *,
        loop: int = 1,
    ) -> None:
        """fixer 修复 → 重扫 → 修复后复测 → 里程碑 → 人工审阅。"""
        all_reviews = "\n\n---\n\n".join(review_texts)
        qg_feedback = self._quality_gate_feedback()
        fixer = self.agent("fixer")
        # 第二轮修复范围小（reviewer 只报了遗漏项），8 轮全量修复
        # 是浪费 —— 限 6 轮
        if loop > 1:
            self._cap_tool_rounds(fixer, 6)
        fixer_note = (
            f"\nNOTE: {discarded} of the review outputs were invalid and discarded — double-check the code yourself for bugs."
            if discarded
            else ""
        )
        rejected = self.blackboard.get("review_rejected", "")
        # tester 在编码阶段的源码 bug 分析（哪个模块/函数坏了）——
        # 拼进 test_output，fixer 不用从 pytest 原始输出里猜
        tester_report = self.blackboard.get("tester_report", "")
        # 上一轮 fixer 未改动任何文件 → 明确警告（防"幻觉修复"循环）
        no_change = self.blackboard.get("fixer_no_change", "")
        # 上一轮平台契约复查发现的缺口（审查-修复闭环）→ 本轮必须补齐
        pending_gaps = self.blackboard.get("contract_gaps", "")
        if pending_gaps:
            self.blackboard["contract_gaps"] = ""
        fixer.react(
            self.prompt(
                "fixer",
                codes=self.files,
                reviews=(all_reviews or "No review issues.") + fixer_note,
                test_output=test_output
                + qg_feedback
                + pending_gaps
                + (f"\nTESTER REPORT:\n{tester_report}" if tester_report else "")
                + (f"\n{no_change}" if no_change else "")
                + (
                    f"\nUSER REJECTED your previous fix: {rejected}" if rejected else ""
                ),
            )
        )
        self.blackboard["review_rejected"] = ""
        if not directory:
            return
        self.blackboard.reload_codes(directory)
        # 审查-修复闭环：修复后平台重跑 AST 契约检查。缺口仍在 →
        # 记录给下一轮 fixer（审查员报过的契约缺口必须真的补上，
        # 不能"文件改动了就算修好"）
        try:
            from codegen.application.phases.coding import contract_gap_text
            gaps = contract_gap_text(directory,
                                     self.blackboard.get("modules", []))
            if gaps and loop < int(load_pipeline_config().get(
                    "verification_rounds", 2) or 2):
                self.blackboard["contract_gaps"] = (
                    gaps.replace("PLATFORM CONTRACT CHECK — the following "
                                 "exports are declared in the design but "
                                 "MISSING from the source.",
                                 "PLATFORM RE-CHECK — these exports were "
                                 "STILL missing after your last fix:")
                )
        except Exception:
            _log.exception("Contract gap re-check failed")
        fixed_bugs, _, _infra = self._run_tests()
        changed = [
            f for f, c in self.blackboard.codes.items() if self._pre_codes.get(f) != c
        ]
        # M1 learn_fix_pattern：修复成功（测试通过）且实际改了文件 →
        # 提取错误签名 + 修复对照入库（只存"验证过的修复"）。写失败
        # 不影响交付（记忆是增强不是依赖）。
        if changed and not fixed_bugs:
            try:
                from memory.domain.extract import extract_fix_pattern
                from memory.infrastructure.chroma_store import MemoryStore
                project = (os.path.basename(directory)
                           .split("_DevForge_", 1)[0])[:80] if directory else ""
                entry = extract_fix_pattern(
                    project or "?",
                    self._pre_codes, dict(self.blackboard.codes),
                    test_output)
                if entry:
                    MemoryStore(
                        chroma_dir=self.blackboard.get("_memory_dir", "")
                    ).write_fix_pattern(entry)
                    print(f"  [Memory] +fix {entry.summary[:60]}", flush=True)
            except Exception:
                _log.exception("Failed to learn fix pattern")
        if review_texts or has_bugs or discarded:
            if changed:
                msg = f"修复完成: {len(changed)} 个文件，等待你审阅"
                self.blackboard["fixer_no_change"] = ""
            else:
                msg = "修复完成，但未改动任何文件（结果存疑）"
                # 传给下一轮 fixer：必须实际修改文件
                self.blackboard["fixer_no_change"] = (
                    "WARNING: your previous fix changed NO files, yet review "
                    "issues remain. Every reported issue must end in a "
                    "write_file call that actually changes the file — or "
                    "explicitly say why no change is needed. Do not claim "
                    "fixes you did not make.")
            if fixed_bugs:
                msg += "（测试仍未通过）"
        else:
            msg = "无需修复"
        HookRegistry.trigger(
            Events.CONVERSATION_TURN,
            agent="Verification",
            content=json.dumps({"message": msg}),
            turn=0,
        )
        self._request_review(directory, changed)

    def _request_review(self, directory: str, changed: list | None = None):
        """fixer 修改过的文件 → diff 送人工审阅（headless 无 ws 自动通过）。

        拒绝 → 记录 review_rejected，下一轮 fixer 带着用户反馈重新修复。
        """
        if changed is None:
            changed = [
                f
                for f, c in self.blackboard.codes.items()
                if self._pre_codes.get(f) != c
            ]
        if not changed:
            return
        diff_lines = []
        for f in changed[:10]:
            old = (self._pre_codes.get(f) or "").splitlines()
            new = (self.blackboard.codes.get(f) or "").splitlines()
            diff_lines.append(f"--- {f}")
            diff_lines.append(f"+++ {f}")
            diff_lines.extend(
                list(difflib.unified_diff(old, new, lineterm="", n=1))[2:]
            )
        from serving.application.ws_manager import ask_approval

        run_id = self.blackboard.get("_run_id", "")
        approved = ask_approval(
            run_id, {"files": changed[:10], "diff": "\n".join(diff_lines)[:8000]}
        )
        HookRegistry.trigger("review_decision", approved=approved, files=changed[:10])
        if not approved:
            self.blackboard["review_rejected"] = (
                f"fixer 的修复被用户拒绝（{', '.join(changed[:5])}）— 重新检查并给出更好的修复"
            )

    def _contracts_text(self) -> str:
        """Format module contracts for reviewer prompts (compact)."""
        contracts = self.blackboard.contracts
        if not contracts:
            return "(no contracts defined)"
        lines = []
        for name, c in contracts.items():
            exports = "; ".join(
                (f"{e.get('name', '')}{e.get('signature', '')}" for e in c.exports)
            )
            lines.append(f"- {name}: {exports or 'no exports'}")
        return "\n".join(lines)

    def _quality_gate_feedback(self) -> str:
        """Format inspector FAIL findings as extra input for the fixer."""
        qg = self.blackboard.get("quality_gate", {})
        if qg.get("verdict") != "FAIL":
            return ""
        missing = [
            f
            for f in qg.get("features") or []
            if isinstance(f, dict) and f.get("status") in ("NO", "PARTIAL")
        ]
        if not missing:
            return ""
        lines = ["\nQUALITY GATE FEEDBACK (must fix before re-review):"]
        for f in missing:
            lines.append(
                f"- [{f.get('status', '?')}] {f.get('name', '?')}: {f.get('notes', '')}"
            )
        return "\n".join(lines)

    def _run_tests(self):
        """Run pytest if test_*.py exist, else the entry point.

        Returns (has_bugs, output, infra_failed)。
        """
        directory = self.blackboard.get("directory", "")
        if not directory:
            return (False, "No project directory — skipping tests.", False)
        return run_project_tests(
            directory,
            self._run_process,
            entry_point=self.blackboard.get("entry_point", ""),
        )

    @staticmethod
    def _cap_tool_rounds(agent, cap: int):
        """限制 agent 的工具轮次上限（≤cap）。防御式访问：
        `_max_tool_rounds` 是 Agent 私有属性，测试用鸭子类型 fake 时
        不存在 —— 不应让阶段代码因缺属性崩溃。"""
        cur = getattr(agent, "_max_tool_rounds", None)
        if cur is not None:
            agent._max_tool_rounds = min(int(cur), cap)

    @staticmethod
    def _run_process(cmd, cwd, timeout: int, env=None):
        """Run *cmd* — 公共实现（codegen.application.process.run_process）。

        *env* 透传：宿主机回退路径注入运行期沙箱 shim 环境（T9-A）——
        此前漏掉该参数导致 Verification 阶段 `run_project_tests` 的
        host fallback 分支 TypeError 崩溃（"unexpected keyword argument
        'env'"），任务在验证阶段直接失败。
        """
        return run_process(cmd, cwd, timeout, env=env)
