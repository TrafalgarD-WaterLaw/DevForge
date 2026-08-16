"""Verification — multi-lens review + test + fix in one cycle."""

import difflib
import json
import logging
import os
import signal
import subprocess
from core.config import load_phases_config
from core.events import Events, HookRegistry
from codegen.application.patterns import parallel
from codegen.domain.phase import Phase
from codegen.domain.registry import register_phase
from codegen.domain.validate import validate_output, validated_react

_log = logging.getLogger(__name__)

def run_project_tests(
    directory: str, run_process, entry_point: str = ""
) -> tuple[bool, str]:
    """Run pytest（或入口）on *directory* — 验证与质检共用。

    Returns (has_bugs, output)。*run_process* 是执行函数
    (cmd, cwd, timeout) → (stdout_bytes, stderr_bytes, returncode)。
    """
    from codegen.infrastructure.tools.registry import (
        cov_args,
        ensure_pytest,
        runtime,
        sandbox_prefix,
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
    if test_files and ensure_pytest(python):
        prefix = sandbox_prefix(directory)
        if prefix:
            cmd = prefix + ["-m", "pytest", "-q", "--no-header", *test_files]
        else:
            cmd = [
                python,
                "-m",
                "pytest",
                "-q",
                "--no-header",
                *cov_args(python, directory),
                *test_files,
            ]
        out, err, code = run_process(cmd, cwd, timeout=120)
        output = (err + out).decode("utf-8", errors="replace")
        if code == 0:
            return (False, "All tests passed.")
        return (True, output.replace(directory.replace("\\", "/") + "/", ""))
    elif test_files:
        _log.warning("pytest unavailable in venv — falling back to entry")
    entry = entry_point or "main.py"
    if not os.path.exists(os.path.join(directory, entry)) and os.path.exists(
        os.path.join(directory, "cli.py")
    ):
        entry = "cli.py"
    out, err, code = run_process([python, entry], cwd, timeout=30)
    output = (err + out).decode("utf-8", errors="replace")
    if code == 0:
        return (False, "The software ran successfully without errors.")
    if output and "Traceback" in output:
        return (True, output.replace(directory.replace("\\", "/") + "/", ""))
    return (bool(output), output or "Process exited with non-zero code.")

@register_phase
class Verification(Phase):
    """Multi-lens review + automated test + fix loop."""

    def run(self):
        directory = self.blackboard.get("directory", "")
        lenses = load_phases_config().get("Verification", {}).get("lenses", [])
        from core.config import load_pipeline_config

        rounds = int(load_pipeline_config().get("verification_rounds", 2) or 2)
        for loop in range(1, rounds + 1):
            HookRegistry.trigger("review_round", phase="Verification", loop=loop)
            self._pre_codes = dict(self.blackboard.codes)
            review_texts, discarded, valid = self._run_review_round(lenses, loop)
            has_bugs, test_output = self._run_tests()
            if not has_bugs and (not review_texts) and (not discarded):
                break
            self._run_fix_round(
                directory, review_texts, discarded, has_bugs, test_output
            )

    def _run_review_round(self, lenses: list, loop: int) -> tuple[list[str], int, int]:
        """多 lens 并行审查 + schema 校验重试。返回
        (review_texts, discarded, valid)。"""
        review_texts = []
        discarded = 0
        valid = 0
        requirements = json.dumps(
            self.blackboard.get("requirements", {}), ensure_ascii=False
        )
        contracts = self._contracts_text()
        for agent, agent_output in parallel(
            [
                (
                    self.agent("reviewer", tag=f"{l['name']}Reviewer"),
                    f"Focus: {l['focus']}\n\n{self.prompt('reviewer', codes=self.files, requirements=requirements, contracts=contracts)}",
                    True,
                )
                for l in lenses
            ]
        ):
            if agent_output is not None and isinstance(agent_output, dict):
                errors = validate_output(agent_output, self.schema("reviewer"))
                if errors:
                    agent_output = validated_react(
                        agent,
                        f"Your previous output failed validation: {'; '.join(errors)}. Re-output as JSON matching the schema exactly.",
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
                review_texts.append(
                    f"### {agent.name}\n"
                    + "\n".join(
                        (
                            f"- [{i['severity']}] {i['file']}:{i['line']} {i['description']}"
                            for i in issues
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
    ) -> None:
        """fixer 修复 → 重扫 → 修复后复测 → 里程碑 → 人工审阅。"""
        all_reviews = "\n\n---\n\n".join(review_texts)
        qg_feedback = self._quality_gate_feedback()
        fixer = self.agent("fixer")
        fixer_note = (
            f"\nNOTE: {discarded} of the review outputs were invalid and discarded — double-check the code yourself for bugs."
            if discarded
            else ""
        )
        rejected = self.blackboard.get("review_rejected", "")
        fixer.react(
            self.prompt(
                "fixer",
                codes=self.files,
                reviews=(all_reviews or "No review issues.") + fixer_note,
                test_output=test_output
                + qg_feedback
                + (
                    f"\nUSER REJECTED your previous fix: {rejected}" if rejected else ""
                ),
            )
        )
        self.blackboard["review_rejected"] = ""
        if not directory:
            return
        self.blackboard.reload_codes(directory)
        fixed_bugs, _ = self._run_tests()
        changed = [
            f for f, c in self.blackboard.codes.items() if self._pre_codes.get(f) != c
        ]
        if review_texts or has_bugs or discarded:
            msg = (
                f"修复完成: {len(changed)} 个文件，等待你审阅"
                if changed
                else "修复完成，但未改动任何文件（结果存疑）"
            )
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

        Returns (has_bugs, output)。提取为模块级 run_project_tests
        。
        """
        directory = self.blackboard.get("directory", "")
        if not directory:
            return (False, "No project directory — skipping tests.")
        return run_project_tests(
            directory,
            self._run_process,
            entry_point=self.blackboard.get("entry_point", ""),
        )

    def _run_process(self, cmd, cwd, timeout: int):
        """Run *cmd*, kill on timeout, return (stdout, stderr, returncode)."""
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                if os.name == "nt"
                else 0,
            )
            try:
                return (*process.communicate(timeout=timeout), process.returncode)
            except subprocess.TimeoutExpired:
                self._kill_process(process)
                try:
                    return (*process.communicate(timeout=5), process.returncode)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return (*process.communicate(), process.returncode)
        except OSError as ex:
            return (b"", str(ex).encode(), 1)

    @staticmethod
    def _kill_process(process):
        """Best-effort termination — never raises ."""
        try:
            if os.name == "nt":
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except OSError:
                    process.kill()
            elif hasattr(os, "killpg"):
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                os.kill(process.pid, signal.SIGTERM)
        except OSError:
            pass

    @staticmethod
    def _trim_paths(output: str, directory: str) -> str:
        return output.replace(directory.replace("\\", "/") + "/", "")
