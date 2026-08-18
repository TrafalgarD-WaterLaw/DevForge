"""Coding — parallel code generation from Design modules."""

import json
import logging
import os
import re
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

    def _contracts_text(self) -> str:
        """Format module contracts for the integrator prompt (compact)."""
        contracts = self.blackboard.contracts
        if not contracts:
            return "(no contracts defined)"
        lines = []
        for name, c in contracts.items():
            exports = "; ".join(
                (f"{e.get('name', '')}{e.get('signature', '')}"
                 for e in c.exports)
            )
            lines.append(f"- {name}: {exports or 'no exports'}")
        return "\n".join(lines)

    def _contract_gap_check(self, modules: list) -> str:
        """平台级契约检查：AST 对比契约 exports vs 源码实际导出。

        coder 在并行开发中反复改契约名（execute_moves→apply_move_plan、
        scan_files→discover_files 已出现 3 次）—— integrator 靠自觉
        检查不可靠，这里平台先做硬检查：契约声明但源码未定义的导出
        生成缺口清单，注入 integrator prompt 强制修复（加别名即可）。
        返回注入文本（无缺口返回 ""）。
        """
        directory = self.blackboard.get("directory", "")
        if not directory:
            return ""
        gaps: list[str] = []
        for mod in modules:
            name = mod.get("name", "")
            src_text = ""
            for f in self._module_files(mod):
                p = os.path.join(directory, f)
                if os.path.exists(p):
                    try:
                        src_text += open(p, encoding="utf-8",
                                         errors="replace").read()
                    except OSError:
                        pass
            defined = set(re.findall(r"^\s*def\s+(\w+)", src_text, re.MULTILINE))
            defined |= set(re.findall(r"^\s*class\s+(\w+)", src_text,
                                      re.MULTILINE))
            for e in mod.get("exports", []):
                en = e.get("name", "")
                if en and en not in defined:
                    gaps.append(f"- {name}.{en} 契约声明但源码未定义")
        if not gaps:
            return ""
        return ("\nPLATFORM CONTRACT CHECK — the following exports are "
                "declared in the design but MISSING from the source. "
                "Implement them now (as `def <name>(...)` or an alias "
                "`<name> = <existing>`):\n"
                + "\n".join(gaps) + "\n")

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
        # stream 参数显式 False：coder 有工具（write_file 等），流式只在
        # 无工具 agent 生效（react 内 stream_ok 条件），传 True 是无效的
        tasks = [
            (
                self.agent("coder", tag=mod.get("name", "coder")),
                self._module_prompt(mod),
                False,
                False,
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
        # 文件列表 + 模块契约 + 平台契约缺口清单给 integrator：
        # 契约说 execute_moves、coder 实现叫 apply_move_plan 这类偏差
        # integrator 是唯一能在联调阶段修复的角色。契约缺口由平台
        # AST 硬检查得出（不依赖 integrator 自觉），强制修复。
        contracts_text = self._contracts_text()
        gaps = self._contract_gap_check(modules)
        if gaps:
            print("  [Coding] 平台契约检查发现缺口，已注入 integrator",
                  flush=True)
        merger.react(self.prompt(
            "integrator", directory=directory, codes=self.files,
            contracts=contracts_text + gaps))
        if directory:
            self.blackboard.reload_codes(directory)
            print(
                f"  [Coding] Files on disk: {list(self.blackboard.codes.keys())}",
                flush=True,
            )
            self._auto_install(directory)
            self._generate_tests(directory)

    @staticmethod
    def _module_files(mod: dict) -> list[str]:
        """模块期望的落盘文件名（设计缺 files 时按惯例 name.py）。"""
        files = [f for f in mod.get("files", []) if isinstance(f, str)]
        return files or [f"{mod.get('name', 'main')}.py"]

    def _module_files_on_disk(self, mod: dict) -> bool:
        """True = 模块的全部期望文件已落盘（可复用/可跳过）。

        完整相对路径匹配（src/cli.py ≠ cli.py），比较前双方 normpath
        规范化 —— 设计给的是正斜杠（"src/main.py"），磁盘 relpath 在
        Windows 是反斜杠（"src\\main.py"），字符串直接比较必然全部误判
        缺失 → 所有模块重试 2 次 → token 白烧 30 万+（c7cb1137 实况）。
        之前按 basename 匹配规避了分隔符问题，但会误判同名文件。
        """
        on_disk = {os.path.normpath(f) for f in self.blackboard.codes.keys()}
        expected = {os.path.normpath(f) for f in self._module_files(mod)}
        if not expected:
            return False
        return expected.issubset(on_disk)

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
                # 重试是新 agent 实例（无对话历史），只给 162 字符的
                # "重新写"消息会让 coder 不知道要写什么契约 —— 附加
                # 完整模块 prompt（模块名/描述/契约/文件路径）
                agent = self.agent("coder", tag=mod.get("name", "coder"))
                from core.config import load_sys_message
                retry_prompt = (
                    self._module_prompt(mod)
                    + "\n\n"
                    + load_sys_message(
                        "coder_retry_missing_module",
                        files=", ".join(self._module_files(mod)))
                )
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
        from codegen.application.process import run_process

        # tester 兜底：react 后磁盘仍无任何 test_*.py（5f7fdc04 实况：
        # tester 卡在 conftest 后停止，测试文件一个都没写，质检直接 NO）
        # → 用专门提示重试 1 次，仍无则把问题留给后续阶段
        has_tests = any(
            f.startswith("test_") and f.endswith(".py")
            for f in os.listdir(directory)
        )
        if not has_tests and tester_report:
            print("  [Tester] 未产出任何测试文件 — 重试 1 次", flush=True)
            tester._max_tool_rounds = min(tester._max_tool_rounds, 6)
            tester_report = self._tester_react_result(
                tester,
                load_sys_message("tester_no_tests_retry"),
            )
            has_tests = any(
                f.startswith("test_") and f.endswith(".py")
                for f in os.listdir(directory)
            )
            if not has_tests:
                print("  [Tester] 重试后仍无测试文件 — 问题留待后续阶段",
                      flush=True)

        has_bugs, output, infra_failed = run_project_tests(
            directory,
            run_process,
            entry_point=self.blackboard.get("entry_point", ""),
        )
        if has_bugs and not infra_failed:
            if _is_collection_error(output or ""):
                tester_report = self._collection_error_report(
                    output or "", directory)
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
                from core.config import load_sys_message
                # 反馈轮限 5 轮：修测试 + 复测足够（之前 12 轮跑满，
                # 反馈轮 10 次调用 ≈ 10 万 tokens 白烧）
                tester._max_tool_rounds = min(tester._max_tool_rounds, 5)
                tester_report = self._tester_react_result(
                    tester,
                    load_sys_message(
                        "tester_failure_feedback",
                        output=(output or "")[:1500]),
                )
                self.blackboard.reload_codes(directory)
                has_bugs, output, infra_failed = run_project_tests(
                    directory,
                    run_process,
                    entry_point=self.blackboard.get("entry_point", ""),
                )
                if has_bugs and not infra_failed:
                    print("  [Tester] 修测试后仍失败 — 源码 bug，报告转 fixer",
                          flush=True)
        # 把 tester 的最终分析留给 fixer（源码 bug 报告不丢失）
        if tester_report:
            self.blackboard["tester_report"] = tester_report

    @staticmethod
    def _collection_error_report(output: str, directory: str) -> str:
        """import/collection 失败 → 直接给 fixer 的报告（tester 改不了源码）。

        平台层自动定位：解析输出里**所有** "cannot import name 'X' from 'Y'"
        （不只第一个 —— fixer 修一个还有下一个、两轮修不好的根因之一），
        对每个名字在项目里 grep 定义位置，生成完整修复清单。
        """
        hints = []
        # 去重（pytest 输出同一错误可能重复出现）
        seen = set()
        for name, mod in sorted(
                set(re.findall(r"cannot import name '(\w+)' from '(\w+)'",
                               output or ""))):
            if (name, mod) in seen:
                continue
            seen.add((name, mod))
            found_in = None
            for root, _dirs, files in os.walk(directory):
                for f in files:
                    if not f.endswith(".py") or f.startswith("test_"):
                        continue
                    path = os.path.join(root, f)
                    try:
                        src = open(path, encoding="utf-8",
                                   errors="replace").read()
                    except OSError:
                        continue
                    if re.search(rf"^\s*def {re.escape(name)}\b", src,
                                 re.MULTILINE):
                        found_in = os.path.relpath(path, directory)
                        break
                if found_in:
                    break
            if found_in:
                hints.append(
                    f"- '{name}' IS defined in '{found_in}' — the import "
                    f"imports it from '{mod}' which is WRONG. Fix the import "
                    "in the caller (or move the function).")
            else:
                hints.append(
                    f"- '{name}' is NOT defined anywhere in the project — "
                    f"it must be ADDED to '{mod}' (check the design contract "
                    "for the intended signature).")
        return ("Tests failed at COLLECTION/IMPORT stage — the test files "
                "cannot import the modules. This is a SOURCE interface "
                "problem (missing module, missing export, wrong signature), "
                "not a test problem. Fix ALL of these:\n"
                + ("\n".join(hints) if hints else "(no import hints found)")
                + "\n\n" + (output or "")[:1500])

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
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if d not in _SKIP]
            # 目录名 = 自有包（src/、pkg/ 或任意包目录布局）——
            # 不硬编码目录名，任何布局的包都不会被当成第三方依赖
            for d in dirs:
                own.add(d)
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
        from codegen.infrastructure.tools.registry import docker_available

        # 执行环境自包含：依赖安装只发生在容器内（docker_script 每次执行
        # 前 pip install -r requirements）。宿主机一律不现场装（不污染
        # 根环境）—— 缺失依赖让测试失败并诚实报告。这里只扫描 imports，
        # 把包名写进 requirements.txt（容器安装 + 交付供应链清单）。
        if not docker_available():
            print("  [Coding] 宿主机模式：跳过依赖自动安装"
                  "（容器内自动装 / 缺失由测试诚实报告）", flush=True)
            return
        own_modules = tuple(
            m.get("name", "") for m in self.blackboard.get("modules", [])
        )
        packages = self._scan_imports(directory, own_modules=own_modules)
        if not packages:
            return
        req_path = os.path.join(directory, "requirements.txt")
        existing_lines: list[str] = []
        if os.path.exists(req_path):
            try:
                existing_lines = open(req_path, encoding="utf-8").read().splitlines()
            except OSError:
                existing_lines = []
        existing_names = {
            line.split("==", 1)[0].strip().lower()
            for line in existing_lines
            if line.strip() and not line.startswith("#")
        }
        added = [p for p in packages if p.lower() not in existing_names]
        if added:
            with open(req_path, "w", encoding="utf-8") as f:
                f.write("\n".join(existing_lines + added) + "\n")
            _log.info(
                "requirements.txt updated with %d packages (%d kept)",
                len(added), len(existing_names),
            )
