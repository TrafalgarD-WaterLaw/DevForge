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

# Python 2 遗留标准库名（3.10+ 的 sys.stdlib_module_names 已移除，
# 但它们不是 PyPI 包，pip install 必然失败）—— 依赖检测必须排除
_LEGACY_STDLIB = {
    "urllib2", "urlparse", "xmlrpclib", "htmlentitydefs", "httplib",
    "dummy_thread", "dummy_threading", "Queue", "StringIO", "ConfigParser",
    "HTMLParser", "thread", "imp", "copy_reg", "cPickle", "cStringIO",
    "anydbm", "dbhash", "dumbdbm", "gdbm", "whichdb", "md5", "sha",
    "mutex", "new", "ni", "popen2", "posixfile", "repr", "sets",
    "sre", "sre_compile", "sre_constants", "sre_parse", "statvfs",
    "symbol", "token", "user", "compiler", "formatter", "fpformat",
    "imageop", "imputil", "linuxaudiodev", "sunaudiodev", "xdrlib",
    "audiodev", "sgmllib", "svn", "nntplib", "SocketServer",
    "BaseHTTPServer", "SimpleHTTPServer", "CGIHTTPServer", "cookielib",
    "gopherlib", "mimetools", "mimify", "multifile", "netrc", "poplib",
    "robotparser", "smtplib", "telnetlib", "urlgrabber", "xmllib",
    "pkg_resources", "pkgutil",
}
# 随解释器自带、非项目依赖的包（pip install 会装错对象/浪费等待）
_BUNDLED_TOOLS = {"pip", "setuptools", "wheel"}
# 无意义 import 名（虚词/超短名 —— 模型偶发在代码里写垃圾 import）
_JUNK_IMPORT_WORDS = {
    "a", "an", "the", "of", "for", "with", "in", "on", "at", "to",
    "from", "within", "into", "this", "that", "other", "another",
    "index", "name", "value", "data", "user", "test", "main", "utils",
    "util", "helper", "helpers", "config", "settings", "db", "app",
}

def _is_junk_import(name: str) -> bool:
    """True = 该 import 名不可能是 PyPI 包（虚词/单字符/带特殊形态）。"""
    return (name.lower() in _JUNK_IMPORT_WORDS
            or len(name) <= 2
            or name in _BUNDLED_TOOLS)

def _is_collection_error(output: str) -> bool:
    """pytest 输出是否为 collection/import 阶段失败（不是断言失败）。

    collection 错误特征：ModuleNotFoundError/ImportError/AttributeError
    在收集阶段、'errors during collection'、'collected 0 items'。
    """
    return any(m in output for m in (
        "ModuleNotFoundError", "ImportError",
        "errors during collection", "collection failed",
        "collected 0 items",
    ))

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
        # Design 指定的落盘路径（如 src/cli.py）必须传给 coder——
        # 否则 coder 凭自己判断全写到项目根目录，src/ 布局形同虚设
        files_text = ", ".join(mod.get("files", [])) or f"{mod.get('name', 'main')}.py"
        return self.prompt(
            "coder",
            module_name=mod.get("name", ""),
            module_desc=mod.get("description", ""),
            module_deps=", ".join(mod.get("depends_on", [])) or "none",
            module_consumers=downstream or "none",
            module_exports=exports_text,
            module_files=files_text,
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
        # 文件列表给 integrator（工具方案：read_many 一次读全部源码）
        merger.react(self.prompt(
            "integrator", directory=directory, codes=self.files))
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
        断言）反馈 tester 重试 ≤1 次。区分两类失败：
        - 测试写错（期望值错/import 错/fixture 误用）→ 修测试
        - 测试揭示源码真 bug → 不弱化断言，留报告给 fixer（修源码）

        成本控制：一次 react 写全部模块测试（read_many 批量读代码 +
        逐文件 write_file + run_tests 验证）—— 每模块单独一轮 react
        会让工具循环 × 模块数（48 次调用、72 万 tokens），单轮收敛
        到 ~10 次调用。
        """
        modules = self.blackboard.get("modules", [])
        if not modules:
            return
        tester = self.agent("tester")
        language = self.blackboard.get("language") or "Python"
        tester_report = ""
        # 全部模块的文件清单 + 契约（工具方案：tester 用 read_many 一次读全部）
        all_files = []
        contracts_lines = []
        for mod in modules:
            name = mod.get("name", "")
            files = [f for f in (mod.get("files") or []) if isinstance(f, str)] \
                or [f"{name or 'main'}.py"]
            all_files.extend(files)
            exports = mod.get("exports", [])
            contracts_lines.append(
                f"- {name}: " + "; ".join(
                    f"{e.get('name', '')}{e.get('signature', '')}"
                    for e in exports
                ) or "- no exports defined"
            )
        tester_report = self._tester_react_result(
            tester,
            self.prompt(
                "tester",
                codes=", ".join(dict.fromkeys(all_files)),
                contracts="\n".join(contracts_lines),
                language=language,
            ),
        )
        from codegen.application.phases.verification import run_project_tests

        # 新测试方案：失败分流，tester 最多反馈 1 次。
        # - import/collection 错误 = 源码接口问题（tester 改不了）→ 直接报告
        # - 断言失败 → 反馈 1 次修测试 → 仍失败 = 源码 bug → 报告转 fixer
        has_bugs, output, infra_failed = run_project_tests(
            directory,
            self._run_process,
            entry_point=self.blackboard.get("entry_point", ""),
        )
        if has_bugs and not infra_failed:
            if _is_collection_error(output or ""):
                tester_report = self._collection_error_report(output or "")
                print("  [Tester] 测试 collection/import 失败 — 源码接口问题，"
                      "直接转 fixer", flush=True)
            else:
                last_line = (
                    (output or "").strip().splitlines()[-1][:100]
                    if (output or "").strip()
                    else "(no output)"
                )
                print(
                    f"  [Tester] 测试失败（反馈 1 次）: {last_line}", flush=True
                )
                tester_report = self._tester_react_result(
                    tester,
                    f"Your tests FAILED with the following output:\n{(output or '')[:1500]}\n\nAnalyze each failure:\n- If the TEST is wrong (wrong expected value, wrong import, fixture misuse): fix the TEST file.\n- If the test reveals a REAL BUG in the source code: DO NOT weaken or delete the test — leave it failing and write a clear report of the broken module/function in your final message (the fixer will repair the source later).\nRe-run with run_tests. You have ONE chance to fix the tests; remaining failures go to the fixer.",
                )
                self.blackboard.reload_codes(directory)
                has_bugs, output, infra_failed = run_project_tests(
                    directory,
                    self._run_process,
                    entry_point=self.blackboard.get("entry_point", ""),
                )
                if has_bugs and not infra_failed:
                    print("  [Tester] 修测试后仍失败 — 源码 bug，报告转 fixer",
                          flush=True)
        # 把 tester 的最终分析留给 fixer（源码 bug 报告不丢失）
        if tester_report:
            self.blackboard["tester_report"] = tester_report

    @staticmethod
    def _collection_error_report(output: str) -> str:
        """import/collection 失败 → 直接给 fixer 的报告（tester 改不了源码）。"""
        return ("Tests failed at COLLECTION/IMPORT stage — the test files "
                "cannot even import the modules. This is a SOURCE interface "
                "problem (missing module, missing export, wrong signature), "
                "not a test problem:\n"
                + output[:1500])

    @staticmethod
    def _tester_react_result(tester, prompt: str) -> str:
        """跑 tester 并提取其最终消息（分析报告会传给 fixer）。"""
        try:
            result = tester.react(prompt)
        except Exception:
            return ""
        if not result:
            return ""
        if isinstance(result, dict):
            content = result.get("content") or result.get("message") or ""
            return str(content)[:2000]
        return str(result)[:2000]

    @staticmethod
    def _scan_imports(directory: str, own_modules: tuple[str, ...] = ()) -> list[str]:
        """Scan non-stdlib imports, excluding the project's own module names
        (B6: 本地模块如 "counter" 不是 PyPI 包，不能 pip install）。

        误报过滤三层：当前版本 stdlib、Python 2 遗留 stdlib 名
        （urllib2/Queue/ConfigParser 等已从 3.10+ 的 stdlib_module_names
        移除，会被当成第三方）、以及无意义词（单字母/虚词——import
        语句解析到 "the"/"within" 说明代码或匹配本身有问题，不应安装）。
        """
        stdlib = set(
            sys.stdlib_module_names if hasattr(sys, "stdlib_module_names") else ()
        )
        stdlib.update(_LEGACY_STDLIB)
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
                                    and not _is_junk_import(name)
                                ):
                                    packages.add(name)
                    except (OSError, UnicodeError):
                        _log.warning("Failed to scan imports in %s", path)
        return sorted(packages)

    def _auto_install(self, directory: str):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from codegen.infrastructure.tools.registry import runtime

        own_modules = tuple(
            m.get("name", "") for m in self.blackboard.get("modules", [])
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

        def install_one(pkg: str) -> tuple[str, bool]:
            """pip install 单个包 → (pkg, ok)。并行 4 路，失败不刷屏。"""
            try:
                r = subprocess.run(
                    [pip, "install", "--no-input", pkg],
                    capture_output=True, timeout=90, check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                return pkg, False
            return pkg, r.returncode == 0

        # 并行安装：串行 40 个假依赖每个等超时是"很慢"的主要来源之一
        results = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(install_one, p): p for p in packages}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception:  # noqa: BLE001 —— 单包失败不拖垮整批安装
                    results.append((futures[fut], False))

        installed: list[str] = []
        failed: list[str] = []
        for pkg, ok in results:
            if not ok:
                failed.append(pkg)
                _log.warning("pip install failed for %s in %s", pkg, directory)
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
        if failed:
            # 失败合并成一条警告，不再每个包刷一行
            print(
                f"  [Coding] ⚠️ {len(failed)} 个依赖安装失败: "
                f"{', '.join(failed[:10])}{' …' if len(failed) > 10 else ''}"
                "（网络/包名问题）— 交付项目可能缺依赖",
                flush=True,
            )
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
