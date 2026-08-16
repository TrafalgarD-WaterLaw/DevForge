"""Test Coding dependency scan — B6: own modules must not be pip-installed."""
from codegen.application.phases.coding import Coding


def test_scan_imports_skips_own_modules(tmp_path):
    (tmp_path / "main.py").write_text(
        "import counter\nimport requests\nfrom json import dumps\n",
        encoding="utf-8")
    packages = Coding._scan_imports(str(tmp_path), own_modules=("counter",))
    # 本地模块 counter 排除；PyPI 包 requests 保留；stdlib json 排除
    assert "counter" not in packages
    assert "requests" in packages
    assert "json" not in packages


def test_scan_imports_skips_venv(tmp_path):
    (tmp_path / "app.py").write_text("import flask\n", encoding="utf-8")
    venv = tmp_path / ".venv" / "Lib" / "site-packages"
    venv.mkdir(parents=True)
    (venv / "torch.py").write_text("import torch\n", encoding="utf-8")
    packages = Coding._scan_imports(str(tmp_path))
    assert packages == ["flask"]  # .venv 下的 torch 不扫描


def test_auto_install_pins_requirements(monkeypatch, tmp_path):
    """安装成功的包带版本 pin 写回 requirements.txt（供应链可复现）。"""
    import subprocess as sp
    from codegen.domain.blackboard import Blackboard
    from codegen.application.phases.coding import Coding
    from codegen.infrastructure.tools.registry import init

    init(project_dir=str(tmp_path), venv_dir="")
    bb = Blackboard()
    bb["modules"] = [{"name": "m"}]
    coding = Coding(bb)
    (tmp_path / "app.py").write_text(
        "import requests\nimport os\n", encoding="utf-8")

    def fake_run(cmd, **kw):
        if cmd[:2] == ["pip", "install"]:
            return sp.CompletedProcess(cmd, 0)
        if cmd[1] == "show":
            return sp.CompletedProcess(
                cmd, 0, stdout="Name: requests\nVersion: 2.31.0\n")
        return sp.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sp, "run", fake_run)
    packages = coding._scan_imports(str(tmp_path), own_modules=("m",))
    assert packages == ["requests"]          # os 是 stdlib，不装
    coding._auto_install(str(tmp_path))
    req = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
    assert "requests==2.31.0" in req         # 版本锁定


def test_scan_imports_excludes_local_files(tmp_path):
    """G2: 本地 .py 文件名（budget.py → budget）不算第三方包，
    不再被误 pip install（此前 coder 自建辅助文件会报 pip install failed）。"""
    (tmp_path / "budget.py").write_text("def b(): pass\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from budget import b\nimport requests\n", encoding="utf-8")
    packages = Coding._scan_imports(str(tmp_path), own_modules=("counter",))
    assert "budget" not in packages      # 本地文件 → 排除
    assert "requests" in packages        # 真第三方包 → 保留


def test_module_files_on_disk(tmp_path):
    """B2: 模块期望文件全部落盘 → 可跳过/可复用。"""
    from codegen.domain.blackboard import Blackboard
    bb = Blackboard()
    bb.codes = {"cli.py": "x", "record.py": "y"}
    coding = Coding(bb)
    assert coding._module_files_on_disk({"name": "cli", "files": ["cli.py"]})
    # 缺 files 字段 → 按 name.py 惯例
    assert not coding._module_files_on_disk({"name": "storage", "files": []})
    bb.codes["storage.py"] = "z"
    assert coding._module_files_on_disk({"name": "storage", "files": []})


def test_retry_missing_modules_writes_retry(monkeypatch, tmp_path):
    """B2: 文件缺失的模块触发重试，重试后落盘即停。"""
    from codegen.domain.blackboard import Blackboard
    bb = Blackboard()
    bb["directory"] = str(tmp_path)
    bb.codes = {}
    coding = Coding(bb)
    retries = []

    class _FakeAgent:
        def __init__(self, *a, **k):
            pass
        name = "storage"
        def react(self, prompt, *, json_mode=False, stream=False):
            retries.append(prompt)
            (tmp_path / "storage.py").write_text("def x(): pass")

    monkeypatch.setattr(coding, "agent", lambda key, tag="": _FakeAgent())
    coding._retry_missing_modules([{"name": "storage", "files": []}])
    assert len(retries) == 1              # 第一次重试即落盘 → 停
    assert "did NOT write" in retries[0]


def test_generate_tests_retries_on_assertion_failure(monkeypatch, tmp_path):
    """T1 闭环：断言失败（不只是 collection 错误）也反馈 tester 重试。"""
    from codegen.domain.blackboard import Blackboard
    from codegen.application.phases.coding import Coding
    import codegen.application.phases.verification as verif_mod

    bb = Blackboard()
    bb["directory"] = str(tmp_path)
    bb["modules"] = [{"name": "m", "exports": [
        {"name": "f", "signature": "() -> int", "description": "d"}]}]
    bb["codes"] = {"m.py": "def f(): return 1"}
    bb["language"] = "Python"
    (tmp_path / "m.py").write_text("def f(): return 1", encoding="utf-8")
    coding = Coding(bb)

    prompts = []

    class _FakeTester:
        name = "tester"
        def react(self, prompt, *, json_mode=False, stream=False):
            prompts.append(prompt)

    monkeypatch.setattr(coding, "agent", lambda key: _FakeTester())
    # 第一次失败（断言），第二次通过
    outputs = iter([
        (True, "FAILED test_m.py::test_f - assert 2 == 1"),
        (False, "All tests passed."),
    ])
    monkeypatch.setattr(verif_mod, "run_project_tests",
                        lambda *a, **k: next(outputs))

    coding._generate_tests(str(tmp_path))

    assert len(prompts) == 2                       # 初次 + 1 次失败反馈
    assert "Analyze each failure" in prompts[1]
    assert "assert 2 == 1" in prompts[1]


def test_generate_tests_no_retry_when_clean(monkeypatch, tmp_path):
    """测试一次通过 → 不反馈重试。"""
    from codegen.domain.blackboard import Blackboard
    from codegen.application.phases.coding import Coding
    import codegen.application.phases.verification as verif_mod

    bb = Blackboard()
    bb["directory"] = str(tmp_path)
    bb["modules"] = [{"name": "m", "exports": []}]
    bb["codes"] = {"m.py": "x=1"}
    coding = Coding(bb)
    prompts = []

    class _FakeTester:
        name = "tester"
        def react(self, prompt, *, json_mode=False, stream=False):
            prompts.append(prompt)

    monkeypatch.setattr(coding, "agent", lambda key: _FakeTester())
    monkeypatch.setattr(verif_mod, "run_project_tests",
                        lambda *a, **k: (False, "All tests passed."))
    coding._generate_tests(str(tmp_path))
    assert len(prompts) == 1                       # 只跑初次，无反馈


def test_product_cache_skips_all_when_pending_empty(monkeypatch, tmp_path):
    """审阅修复：全部模块已落盘 → tasks 为空（不重新生成）。"""
    from codegen.domain.blackboard import Blackboard
    from codegen.application.phases.coding import Coding
    import codegen.application.phases.coding as coding_mod

    bb = Blackboard()
    bb["directory"] = str(tmp_path)
    bb["modules"] = [{"name": "cli", "files": ["cli.py"]}]
    bb["codes"] = {"cli.py": "x = 1"}
    (tmp_path / "cli.py").write_text("x = 1", encoding="utf-8")
    captured = {}
    def fake_parallel(tasks):
        captured["n"] = len(tasks)
        return []
    monkeypatch.setattr(coding_mod, "parallel", fake_parallel)
    monkeypatch.setattr(Coding, "_retry_missing_modules",
                        lambda self, mods: None)
    monkeypatch.setattr(Coding, "_generate_tests", lambda self, d: None)
    monkeypatch.setattr(Coding, "_auto_install", lambda self, d: None)
    # integrator 不进 LLM：替换 agent 返回 fake
    monkeypatch.setattr(
        Coding, "agent",
        lambda self, key, tag="": type("A", (), {
            "react": lambda self2, p, **k: None, "name": tag or key})())
    Coding(bb).run()
    assert captured.get("n") == 0        # 全部复用 → 不生成任何模块
