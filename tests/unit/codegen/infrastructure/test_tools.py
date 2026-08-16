"""Test tool registry and built-in tools."""
import os
import tempfile

from codegen.infrastructure.tools.registry import _registry, describe, init, runtime


def setup_module():
    """Import tool modules to trigger registration."""
    import codegen.infrastructure.tools.file_tools
    import codegen.infrastructure.tools.code_tools
    import codegen.infrastructure.tools.plan_tools
    import codegen.infrastructure.tools.web_tools


def _setup_runtime(tmpdir):
    """Initialise a tool runtime pointed at *tmpdir*."""
    init(project_dir=tmpdir)


def test_all_tools_registered():
    tools = list(_registry.tools.keys())
    assert "read_file" in tools
    assert "write_file" in tools
    assert "run_code" in tools
    assert "search_web" in tools
    assert "list_files" in tools
    assert "todo_write" in tools


def test_read_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        _setup_runtime(tmpdir)
        test_file = os.path.join(tmpdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("hello world")
        result = runtime().execute("read_file", {"filename": "test.txt"})
        assert "hello world" in result


def test_read_nonexistent_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        _setup_runtime(tmpdir)
        result = runtime().execute("read_file", {"filename": "nope.txt"})
        assert "Error" in result or "does not exist" in result


def test_write_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        _setup_runtime(tmpdir)
        runtime().execute("write_file", {"filename": "out.txt", "content": "data"})
        with open(os.path.join(tmpdir, "out.txt")) as f:
            assert f.read() == "data"


def test_list_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        _setup_runtime(tmpdir)
        open(os.path.join(tmpdir, "a.py"), "w").close()
        open(os.path.join(tmpdir, "b.py"), "w").close()
        result = runtime().execute("list_files", {"pattern": "*.py"})
        assert "a.py" in result
        assert "b.py" in result


def test_list_files_rejects_traversal_and_absolute():
    """B6: glob 逃逸 — '..' 与绝对路径模式直接拒绝，不进入 glob 拼接。"""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        _setup_runtime(tmpdir)
        for bad in ("../secret.txt", "..", "C:\\Windows\\win.ini",
                    "/etc/passwd", "sub/../x.py"):
            result = runtime().execute("list_files", {"pattern": bad})
            assert "Error" in result, f"pattern {bad!r} not rejected: {result}"


def test_read_file_rejects_traversal():
    """B9: read_file 拒绝 '../' 与绝对路径（_safe_path）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        _setup_runtime(tmpdir)
        secret = os.path.join(tmpdir, "..", "secret.txt")
        with open(secret, "w") as f:
            f.write("top secret")
        result = runtime().execute("read_file", {"filename": "../secret.txt"})
        assert result.startswith("Error:")
        result = runtime().execute(
            "read_file", {"filename": os.path.abspath(secret)})
        assert result.startswith("Error:")
        result = runtime().execute("read_file", {"filename": ".."})
        assert result.startswith("Error:")


def test_run_tests_tool_registered_and_runs_pytest():
    """B2: run_tests 走 pytest（-m pytest），不是 python test_x.py。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        _setup_runtime(tmpdir)
        with open(os.path.join(tmpdir, "test_ok.py"), "w") as f:
            f.write("def test_pass():\n    assert 1 + 1 == 2\n")
        result = runtime().execute("run_tests", {})
        assert "Execution successful" in result


def test_run_tests_failure_raises_tool_error():
    """B2: run_tests 非零退出 → RuntimeError → ToolError 返回，含 stderr。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        _setup_runtime(tmpdir)
        with open(os.path.join(tmpdir, "test_bad.py"), "w") as f:
            f.write("def test_fail():\n    assert False\n")
        result = runtime().execute("run_tests", {})
        assert "ToolError" in result


def test_unknown_tool():
    init()
    result = runtime().execute("nonexistent_tool", {})
    assert "unknown tool" in result.lower()


def test_describe():
    schema = describe()
    assert len(schema) >= 6
    for tool_schema in schema:
        assert tool_schema["type"] == "function"
        assert "name" in tool_schema["function"]
        assert "description" in tool_schema["function"]
        assert "parameters" in tool_schema["function"]


def test_require_confirmation_flag_removed():
    """B7: dead require_confirmation plumbing removed — write_file must not
    carry a confirmation flag nothing ever reads."""
    for name in ("write_file", "read_file", "run_code", "run_tests"):
        assert not hasattr(_registry.tools[name], "require_confirmation")


def test_run_code_stdin_blocking_script_returns_fast():
    """生成物读 stdin 时立即 EOF 返回，不阻塞 30s（回归：验证阶段慢的根因）。"""
    import time
    with tempfile.TemporaryDirectory() as tmpdir:
        _setup_runtime(tmpdir)
        # 模拟生成的 CLI：从 stdin 读取（无输入时会阻塞——除非 stdin 被置空）
        with open(os.path.join(tmpdir, "main.py"), "w") as f:
            f.write("import sys\ndata = sys.stdin.read()\nprint(len(data))\n")
        start = time.monotonic()
        result = runtime().execute("run_code", {"entry": "main.py"})
        elapsed = time.monotonic() - start
        assert elapsed < 10, f"run_code 阻塞了 {elapsed:.1f}s — stdin 未被置空"
        assert "Execution successful" in result
        assert "0" in result  # stdin 为空 → 读到 0 字符


def test_ensure_venv_idempotent_and_venv_python():
    """venv 兜底：python 存在时幂等；venv_python 返回 venv 解释器路径。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        venv_dir = os.path.join(tmpdir, ".venv")
        os.makedirs(os.path.join(venv_dir, "Scripts"), exist_ok=True)
        with open(os.path.join(venv_dir, "Scripts", "python.exe"), "w") as f:
            f.write("")
        from codegen.infrastructure.tools.registry import ensure_venv, init
        ensure_venv(venv_dir)   # 已存在 → 幂等，不抛异常
        init(project_dir=tmpdir, venv_dir=venv_dir)
        assert runtime().venv_python().endswith("python.exe")


def test_ensure_pytest_missing_interpreter_returns_false():
    """ensure_pytest：解释器不存在 → 探测失败返回 False（不抛、不装）。"""
    from codegen.infrastructure.tools.registry import ensure_pytest
    assert ensure_pytest(os.path.join("Z:", "nonexistent", "python.exe")) is False


def test_ensure_pytest_system_python_has_pytest():
    """ensure_pytest：当前解释器有 pytest → 直接通过（探测成功即缓存）。"""
    import sys
    from codegen.infrastructure.tools.registry import ensure_pytest
    assert ensure_pytest(sys.executable) is True


def test_tool_result_truncated_at_cap(tmp_path):
    """工具结果超过 6000 字符必须截断回填（防大文件/长输出爆 LLM 上下文）。"""
    import os
    from codegen.infrastructure.tools.registry import MAX_TOOL_RESULT_CHARS, init, runtime
    _setup_runtime(tmp_path)
    with open(os.path.join(tmp_path, "big.py"), "w", encoding="utf-8") as f:
        f.write("# line\n" * 3000)          # ~21k 字符
    result = runtime().execute("read_file", {"filename": "big.py"})
    assert len(result) <= MAX_TOOL_RESULT_CHARS + 100
    assert "已截断" in result
    # 小结果不截断
    with open(os.path.join(tmp_path, "small.py"), "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    small = runtime().execute("read_file", {"filename": "small.py"})
    assert "已截断" not in small
    assert "x = 1" in small


def test_read_file_cached_and_invalidated_on_write(tmp_path):
    """read_file 结果缓存；write_file 后失效（读到新内容而非旧缓存）。"""
    import os
    with open(os.path.join(tmp_path, "f.txt"), "w", encoding="utf-8") as f:
        f.write("v1")
    _setup_runtime(tmp_path)
    rt = runtime()
    assert rt.execute("read_file", {"filename": "f.txt"}) == "v1"
    assert rt.execute("read_file", {"filename": "f.txt"}) == "v1"   # 缓存命中
    rt.execute("write_file", {"filename": "f.txt", "content": "v2"})
    assert rt.execute("read_file", {"filename": "f.txt"}) == "v2"   # 写后失效
    assert rt.execute("read_file", {"filename": "other.txt"}) \
        .startswith("Error")                                       # 未缓存 key 正常执行


class _FakeResp:
    def __init__(self, text="", data=None):
        self.text = text
        self._data = data or {}
    def json(self):
        return self._data
    def raise_for_status(self):
        pass


def test_search_web_falls_back_to_html_engine(monkeypatch):
    """Instant Answer 空结果 → 回退 HTML 引擎；两引擎都试过才放弃。"""
    import codegen.infrastructure.tools.web_tools as wt
    if wt.requests is None:
        return  # requests 未安装 —— 跳过（工具会给出明确错误）
    html = ('<a class="result__a" href="//duckduckgo.com/l/?'
            'uddg=https%3A%2F%2Fdocs.python.org%2F3%2F">Python docs</a>')
    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        if "api.duckduckgo.com" in url:
            return _FakeResp(data={"AbstractText": ""})     # 空摘要 → 触发回退
        return _FakeResp(text=html)

    monkeypatch.setattr(wt.requests, "get", fake_get)
    result = wt.search_web("python docs")
    assert "Python docs" in result
    assert "docs.python.org" in result
    assert len(calls) == 2                                   # instant → html


def test_ddg_html_parses_titles_snippets_urls(monkeypatch):
    """HTML 引擎解析：标题 + 真实 URL（uddg 解码）+ 摘要去标签。"""
    import codegen.infrastructure.tools.web_tools as wt
    if wt.requests is None:
        return
    html = ('<a class="result__a" href="//duckduckgo.com/l/?'
            'uddg=https%3A%2F%2Fx.dev%2Fpage">Title A</a>\n'
            '<a class="result__snippet">Some <b>bold</b> snippet</a>')
    monkeypatch.setattr(wt.requests, "get",
                        lambda url, **kw: _FakeResp(text=html))
    out = wt._ddg_html("q")
    assert out[0].startswith("Title A — https://x.dev/page")
    assert "bold" in out[0]
    assert "<b>" not in out[0]


def test_sandbox_prefix_default_empty():
    """默认直跑：sandbox 未配置 → 无前缀。"""
    from codegen.infrastructure.tools.registry import sandbox_prefix
    assert sandbox_prefix("C:/proj") == []


def test_sandbox_prefix_docker(monkeypatch):
    """config tools.sandbox=docker + docker 可用 → docker run 前缀（/work）。"""
    from codegen.infrastructure.tools.registry import sandbox_prefix
    monkeypatch.setattr("core.config.load_pipeline_config",
                        lambda: {"tools": {"sandbox": "docker"}})
    monkeypatch.setattr("codegen.infrastructure.tools.registry._docker_ok", True)
    prefix = sandbox_prefix("C:/proj")
    assert prefix[0] == "docker"
    assert "C:/proj:/work" in prefix
    assert prefix[-1] == "python"


def test_sandbox_prefix_docker_unavailable_falls_back(monkeypatch):
    """docker 不可用 → 回退宿主机执行（不再让 run_code 报 docker 错误）。"""
    from codegen.infrastructure.tools.registry import sandbox_prefix
    monkeypatch.setattr("core.config.load_pipeline_config",
                        lambda: {"tools": {"sandbox": "docker"}})
    monkeypatch.setattr("codegen.infrastructure.tools.registry._docker_ok", False)
    assert sandbox_prefix("C:/proj") == []


def test_cov_args_venv_only():
    """覆盖率参数只在项目 venv（装有 pytest-cov）下启用。"""
    from codegen.infrastructure.tools.registry import cov_args, init
    init(venv_dir="C:/venv")
    assert cov_args("C:/venv/Scripts/python.exe") == \
        ["--cov=.", "--cov-report=term-missing:skip-covered"]
    assert cov_args("D:/system/python.exe") == []
    init(venv_dir="")   # 复位


def test_run_id_filter_attaches_run_id():
    """结构化日志：记录带 [run=xxxx]（contextvar 读取）。"""
    import logging
    from core.context import set_current_run
    from core.logging import RunIdFilter

    set_current_run("abc123")
    rec = logging.LogRecord("t", logging.INFO, "", 0, "msg", None, None)
    assert RunIdFilter().filter(rec)
    assert rec.run_id == "abc123"

    set_current_run("")
    rec2 = logging.LogRecord("t", logging.INFO, "", 0, "m", None, None)
    RunIdFilter().filter(rec2)
    assert rec2.run_id == "-"


def test_ensure_pytest_probes_cov(monkeypatch):
    """H2: 探测必须同时确认 pytest_cov —— 旧 venv 只有 pytest 没有
    pytest-cov 时不能缓存命中跳过补装（--cov-data-file unrecognized）。"""
    import subprocess
    from codegen.infrastructure.tools.registry import _pytest_verified, ensure_pytest
    _pytest_verified.discard("C:/venv/py.exe")

    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        # 第一次探测：pytest 在但 pytest_cov 缺失 → 触发安装
        if cmd[1] == "-c":
            return subprocess.CompletedProcess(cmd, 1)
        return subprocess.CompletedProcess(cmd, 0)   # pip install 成功
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert ensure_pytest("C:/venv/py.exe") is True
    assert any("-c" in c[1] for c in calls)          # import 探测
    assert any("install" in c for c in calls)        # 触发补装
    assert "C:/venv/py.exe" in _pytest_verified
    _pytest_verified.discard("C:/venv/py.exe")
