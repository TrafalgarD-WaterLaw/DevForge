"""Coding — parallel code generation from Design modules."""

import json
import logging
import os
import re
import subprocess
import sys
from core.events import Events, HookRegistry
from codegen.application.patterns import parallel
from codegen.domain.phase import Phase
from codegen.domain.registry import register_phase

_log = logging.getLogger(__name__)

@register_phase
class Coding(Phase):
    """Build each module from Design in parallel, then merge."""

    def _module_prompt(self, mod: dict) -> str:
        downstream = ", ".join(
            self.blackboard.get_downstream_modules(mod.get("name", ""))
        )
        exports = mod.get("exports", [])
        exports_text = (
            "\n".join(
                (
                    f"  - {e['name']}{e['signature']} — {e['description']}"
                    for e in exports
                )
            )
            if exports
            else "No public API defined — design your own."
        )
        return self.prompt(
            "coder",
            module_name=mod.get("name", ""),
            module_desc=mod.get("description", ""),
            module_deps=", ".join(mod.get("depends_on", [])) or "none",
            module_consumers=downstream or "none",
            module_exports=exports_text,
            language=self.blackboard.get("language") or "Python",
        )

    def run(self):
        modules = self.blackboard.get("modules", [])
        total_modules = len(modules)
        directory = self.blackboard.get("directory", "")
        modules = self._filter_pending_modules(modules, directory)
        self._generate_modules(modules)
        self._finalize(directory, total_modules, modules)

    def _filter_pending_modules(self, modules: list, directory: str) -> list:
        """B2 产物缓存：重跑（start_from）时已落盘的模块跳过重新生成。

        审阅修复：`pending or modules` 在 pending 为空时回落到原 modules
        → 重跑时全部重新生成，产物缓存形同虚设。直接取 pending（空 =
        全部复用，parallel([]) 跳过生成）。
        """
        if directory:
            self.blackboard.reload_codes(directory)
        pending = []
        for mod in modules:
            if self._module_files_on_disk(mod):
                print(f"  [Coding] 产物已存在，跳过 {mod.get('name', '?')}", flush=True)
            else:
                pending.append(mod)
        if pending and len(pending) < len(modules):
            print(
                f"  [Coding] 复用 {len(modules) - len(pending)} 个已生成模块，生成 {len(pending)} 个缺失模块",
                flush=True,
            )
        return pending

    def _generate_modules(self, modules: list) -> None:
        """Build each module in parallel。"""
        tasks = [
            (
                self.agent("coder", tag=mod.get("name", "coder")),
                self._module_prompt(mod),
                False,
                True,
            )
            for mod in modules
        ]
        for agent, coder_output in parallel(tasks):
            if coder_output is None:
                continue
            text = json.dumps(coder_output, ensure_ascii=False)
            print(f"  [Coding] {agent.name}: {len(text)} chars", flush=True)
            HookRegistry.trigger(
                "coding_progress",
                agent=agent.name,
                lines=len(text.split("\n")),
                chars=len(text),
            )

    def _finalize(self, directory: str, total_modules: int, modules: list) -> None:
        """重扫/重试/里程碑/整合联调/依赖安装/测试生成。"""
        if directory:
            self.blackboard.reload_codes(directory)
        if not self.blackboard.codes:
            print(
                "  [Coding] WARNING: 未生成任何代码文件 — 检查 coder 输出", flush=True
            )
        self._retry_missing_modules(modules)
        HookRegistry.trigger(
            Events.CONVERSATION_TURN,
            agent="Coding",
            content=json.dumps(
                {
                    "message": f"编码完成: {total_modules} 个模块 — {', '.join((m.get('name', '') for m in modules))}"
                }
            ),
            turn=0,
        )
        HookRegistry.trigger("integration_start")
        merger = self.agent("integrator")
        merger.react(self.prompt("integrator", directory=directory))
        if directory:
            self.blackboard.reload_codes(directory)
            print(
                f"  [Coding] Files on disk: {list(self.blackboard.codes.keys())}",
                flush=True,
            )
            self._auto_install(directory)
            self._generate_tests(directory)

    @staticmethod
    def _run_process(cmd, cwd, timeout):
        """同 Verification._run_process 的轻量版（Coding 的 tester 复验用）。"""
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
                try:
                    process.kill()
                except OSError:
                    pass
                out, err = process.communicate()
                return (out, err, process.returncode)
        except OSError as ex:
            return (b"", str(ex).encode(), 1)

    @staticmethod
    def _module_files(mod: dict) -> list[str]:
        """模块期望的落盘文件名（设计缺 files 时按惯例 name.py）。"""
        files = [f for f in mod.get("files", []) if isinstance(f, str)]
        return files or [f"{mod.get('name', 'main')}.py"]

    def _module_files_on_disk(self, mod: dict) -> bool:
        """True = 模块的全部期望文件已落盘（可复用/可跳过）。"""
        names = {os.path.basename(f) for f in self.blackboard.codes.keys()}
        expected = {os.path.basename(f) for f in self._module_files(mod)}
        return bool(expected) and expected.issubset(names)

    def _retry_missing_modules(self, modules: list[dict]):
        """文件未落盘的模块用新 coder 重试（≤2 次）—— coder 偶发输出
        无效/没写完时不再静默丢失该模块。"""
        directory = self.blackboard.get("directory", "")
        if not directory:
            return
        for mod in modules:
            if self._module_files_on_disk(mod):
                continue
            for attempt in (1, 2):
                print(
                    f"  [Coding] {mod.get('name', '?')} 文件缺失 — 重试第 {attempt} 次",
                    flush=True,
                )
                agent = self.agent("coder", tag=mod.get("name", "coder"))
                retry_prompt = f"Your previous attempt did NOT write the required files to disk ({', '.join(self._module_files(mod))}). Write the COMPLETE module now using write_file — no placeholders, no partial output."
                agent.react(retry_prompt, stream=True)
                self.blackboard.reload_codes(directory)
                if self._module_files_on_disk(mod):
                    break

    def _generate_tests(self, directory: str):
        """Tester agent writes test_*.py for every module contract.

        测试质量闭环：写完必须真跑通 —— 任何失败（collection/import/
        断言）反馈 tester 重试 ≤2 次。区分两类失败：
        - 测试写错（期望值错/import 错/fixture 误用）→ 修测试
        - 测试揭示源码真 bug → 不弱化断言，留报告给 fixer（修源码）
        """
        modules = self.blackboard.get("modules", [])
        if not modules:
            return
        contracts = "\n".join(
            (
                f"- {m.get('name', '?')}: "
                + "; ".join(
                    (
                        f"{e.get('name', '')}{e.get('signature', '')}"
                        for e in m.get("exports", [])
                    )
                )
                or "- no exports defined"
                for m in modules
            )
        )
        tester = self.agent("tester")
        tester.react(
            self.prompt(
                "tester",
                codes=self.codes,
                contracts=contracts,
                language=self.blackboard.get("language") or "Python",
            )
        )
        self.blackboard.reload_codes(directory)
        from codegen.application.phases.verification import run_project_tests

        for attempt in (1, 2):
            has_bugs, output = run_project_tests(
                directory,
                self._run_process,
                entry_point=self.blackboard.get("entry_point", ""),
            )
            if not has_bugs:
                break
            last_line = (
                output.strip().splitlines()[-1][:100]
                if output.strip()
                else "(no output)"
            )
            print(
                f"  [Tester] 测试失败（第 {attempt} 次反馈）: {last_line}", flush=True
            )
            tester.react(
                f"Your tests FAILED with the following output:\n{output[:1500]}\n\nAnalyze each failure:\n- If the TEST is wrong (wrong expected value, wrong import, fixture misuse, collection error): fix the TEST file.\n- If the test reveals a REAL BUG in the source code: DO NOT weaken or delete the test — leave it failing and write a clear report of the broken module/function in your final message (the fixer will repair the source later).\nRe-run with run_tests. Every remaining failure must be either fixed or explicitly reported."
            )
            self.blackboard.reload_codes(directory)

    @staticmethod
    def _scan_imports(directory: str, own_modules: tuple[str, ...] = ()) -> list[str]:
        """Scan non-stdlib imports, excluding the project's own module names
        (B6: 本地模块如 "counter" 不是 PyPI 包，不能 pip install）。"""
        stdlib = (
            sys.stdlib_module_names if hasattr(sys, "stdlib_module_names") else set()
        )
        own = set(own_modules)
        packages: set[str] = set()
        import_re = re.compile("^\\s*(?:from|import)\\s+(\\w+)", re.MULTILINE)
        _SKIP = {".venv", "__pycache__", ".git", ".task_outputs", ".devforge"}
        for root, _dirs, files in os.walk(directory):
            _dirs[:] = [d for d in _dirs if d not in _SKIP]
            for f in files:
                if f.endswith(".py"):
                    own.add(f[:-3])
                    try:
                        path = os.path.join(root, f)
                        with open(path, encoding="utf-8", errors="replace") as fh:
                            for m in import_re.finditer(fh.read()):
                                name = m.group(1)
                                if (
                                    name not in stdlib
                                    and (not name.startswith("_"))
                                    and (name not in own)
                                ):
                                    packages.add(name)
                    except (OSError, UnicodeError):
                        _log.warning("Failed to scan imports in %s", path)
        return sorted(packages)

    def _auto_install(self, directory: str):
        from codegen.infrastructure.tools.registry import runtime

        own_modules = tuple(
            (m.get("name", "") for m in self.blackboard.get("modules", []))
        )
        packages = self._scan_imports(directory, own_modules=own_modules)
        if not packages:
            return
        venv_dir = runtime().ctx.venv_dir
        pip = (
            os.path.join(venv_dir, "Scripts" if os.name == "nt" else "bin", "pip")
            if venv_dir
            else "pip"
        )
        installed: list[str] = []
        for pkg in packages:
            try:
                r = subprocess.run(
                    [pip, "install", pkg], capture_output=True, timeout=60, check=False
                )
            except (subprocess.TimeoutExpired, OSError):
                _log.warning("pip install failed for %s in %s", pkg, directory)
                print(
                    f"  [Coding] ⚠️ 依赖安装失败: {pkg}（网络或包名问题）— 交付项目可能缺依赖，requirements.txt 未记录",
                    flush=True,
                )
                continue
            if r.returncode != 0:
                _log.warning("pip install failed for %s in %s", pkg, directory)
                print(
                    f"  [Coding] ⚠️ 依赖安装失败: {pkg}（pip 退出码 {r.returncode}）— 交付项目可能缺依赖",
                    flush=True,
                )
                continue
            version = ""
            try:
                show = subprocess.run(
                    [pip, "show", pkg],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
                for line in show.stdout.splitlines():
                    if line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip()
                        break
            except (subprocess.TimeoutExpired, OSError):
                pass
            installed.append(f"{pkg}=={version}" if version else pkg)
        if installed:
            req_path = os.path.join(directory, "requirements.txt")
            existing = ""
            if os.path.exists(req_path):
                try:
                    existing = open(req_path, encoding="utf-8").read().rstrip()
                except OSError:
                    existing = ""
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(
                    existing + ("\n" if existing else "") + "\n".join(installed) + "\n"
                )
            _log.info(
                "requirements.txt updated with %d pinned packages", len(installed)
            )
