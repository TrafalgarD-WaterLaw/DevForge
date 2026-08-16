"""Test pipeline execution."""
import os

import pytest
from codegen.domain.blackboard import Blackboard
from core.events import Events, HookRegistry
from codegen.domain.registry import PhaseRegistry
from codegen.application.pipeline import Pipeline


@pytest.fixture(autouse=True)
def clean_registry():
    PhaseRegistry._registry.clear()
    yield
    PhaseRegistry._registry.clear()


@pytest.fixture(autouse=True)
def clean_hooks():
    HookRegistry.clear()
    yield
    HookRegistry.clear()


def test_unknown_phase_raises():
    with pytest.raises(KeyError):
        Pipeline(Blackboard()).run(["nonexistent"])


def test_codes_roundtrip_through_disk(tmp_path):
    """B9: generated code structure survives write → reload intact."""
    bb = Blackboard()
    bb.codes = {
        "main.py": "print('hi')\n",
        "src/utils.py": "def helper():\n    return 42\n",
    }
    bb.write_files(str(tmp_path))
    assert (tmp_path / "main.py").exists()
    assert (tmp_path / "src" / "utils.py").read_text(encoding="utf-8") \
        == "def helper():\n    return 42\n"

    # reload_codes re-scans disk, skipping test_ files and venvs
    (tmp_path / "test_main.py").write_text("def test_x(): pass", encoding="utf-8")
    reloaded = Blackboard()
    reloaded.reload_codes(str(tmp_path))
    assert set(reloaded.codes) == {
        "main.py", os.path.join("src", "utils.py")}  # 平台分隔符
    assert reloaded.codes["main.py"] == "print('hi')\n"


def test_write_files_creates_subdirs(tmp_path):
    """B12: write_files 为每个文件创建其父目录，而非仅顶层目录。"""
    bb = Blackboard()
    bb.codes = {"src/main.py": "x = 1", "pkg/utils/helpers.py": "def h(): pass"}
    bb.docs = {"docs/api.md": "# API"}
    bb.write_files(str(tmp_path))
    assert (tmp_path / "src" / "main.py").read_text(encoding="utf-8") == "x = 1"
    assert (tmp_path / "pkg" / "utils" / "helpers.py").exists()
    assert (tmp_path / "docs" / "api.md").exists()


# ── B2: run-id contextvar survives parallel() workers ──

def test_parallel_workers_forward_tool_events():
    """Tool events fired inside ThreadPoolExecutor workers must carry the
    run id — otherwise _ws_forward drops them (B2)."""
    from core.events import _ws_forward
    from codegen.application.patterns import parallel
    from core.context import set_current_run
    from serving.application.ws_manager import _active_runs, init_run

    HookRegistry.on("*", _ws_forward)
    run_id = "b2-test"
    init_run(run_id)
    set_current_run(run_id)

    class _FakeAgent:
        name = "coder"
        def react(self, prompt, *, json_mode=False, stream=False):
            # Fired inside the pool worker thread
            HookRegistry.trigger(Events.TOOL_PRE_USE, tool="write_file",
                                 args={}, agent=self.name)
            return {"message": "ok"}

    parallel([(_FakeAgent(), "build module")])

    events = _active_runs[run_id]["events"]
    assert any(e.get("event") == "tool_pre_use" for e in events)


# ── Phase retry (C4) ────────────────────────────────────

class _FailsOncePhase:
    """Fake phase that raises on first run, succeeds after."""
    _runs = {}

    def __init__(self, blackboard):
        self.blackboard = blackboard

    def run(self):
        key = type(self).__name__
        type(self)._runs[key] = type(self)._runs.get(key, 0) + 1
        if type(self)._runs[key] == 1:
            raise RuntimeError("transient failure")


def test_phase_retry_recovers(tmp_path, monkeypatch):
    _FailsOncePhase._runs = {}
    PhaseRegistry.register("Fragile", _FailsOncePhase)

    # Pipeline reads phase_retries from default.json — monkeypatch the
    # reference bound inside codegen.application.pipeline.
    import codegen.application.pipeline as pipe_mod
    monkeypatch.setattr(
        pipe_mod, "load_pipeline_config",
        lambda *a, **k: {"phase_retries": {"Fragile": 1}})

    events = []
    HookRegistry.on(Events.PHASE_RETRY, lambda ev, **kw: events.append(kw))

    bb = Blackboard()
    bb["directory"] = ""
    Pipeline(bb).run(["Fragile"])

    assert _FailsOncePhase._runs["_FailsOncePhase"] == 2  # fail then succeed
    assert len(events) == 1
    assert events[0]["phase"] == "Fragile"


def test_phase_retry_exhausted_raises(monkeypatch):
    class _AlwaysFails(_FailsOncePhase):
        def run(self):
            type(self)._runs[type(self).__name__] = \
                type(self)._runs.get(type(self).__name__, 0) + 1
            raise RuntimeError("transient failure")
    _AlwaysFails._runs = {}
    PhaseRegistry.register("AlwaysFragile", _AlwaysFails)

    import codegen.application.pipeline as pipe_mod
    monkeypatch.setattr(
        pipe_mod, "load_pipeline_config",
        lambda *a, **k: {"phase_retries": {"AlwaysFragile": 1}})

    bb = Blackboard()
    bb["directory"] = ""
    with pytest.raises(RuntimeError):
        Pipeline(bb).run(["AlwaysFragile"])
    assert _AlwaysFails._runs["_AlwaysFails"] == 2  # ran + 1 retry


# ── QualityGate loop (A2) ───────────────────────────────

class _RecordingPhase:
    """Fake phase that records how many times it ran."""
    _counts = {}

    def __init__(self, blackboard):
        self.blackboard = blackboard

    def run(self):
        type(self)._counts[type(self).__name__] = \
            type(self)._counts.get(type(self).__name__, 0) + 1


class _AlwaysFailQualityGate(_RecordingPhase):
    """Fake QualityGate that always reports FAIL."""

    def run(self):
        super().run()
        self.blackboard["quality_gate"] = {
            "verdict": "FAIL",
            "features": [{"name": "f", "status": "NO", "notes": "missing"}],
            "score": 30,
        }


class _AlwaysPassQualityGate(_RecordingPhase):
    """Fake QualityGate that always reports PASS."""

    def run(self):
        super().run()
        self.blackboard["quality_gate"] = {
            "verdict": "PASS", "features": [], "score": 100,
        }


class _ThenPassQualityGate(_RecordingPhase):
    """Fake QualityGate that FAILs once, then passes."""

    def run(self):
        super().run()
        n = type(self)._counts.get(type(self).__name__, 0)
        self.blackboard["quality_gate"] = {
            "verdict": "FAIL" if n <= 1 else "PASS",
            "features": [{"name": "f", "status": "NO", "notes": "missing"}],
            "score": 30 if n <= 1 else 90,
        }


class _ThenCleanWarnQualityGate(_RecordingPhase):
    """Fake QualityGate: WARN 含未达标项 → 再跑一次后全部完成（WARN 无 missing）。"""

    def run(self):
        super().run()
        n = type(self)._counts.get(type(self).__name__, 0)
        if n <= 1:
            self.blackboard["quality_gate"] = {
                "verdict": "WARN",
                "features": [{"name": "f", "status": "PARTIAL",
                              "notes": "incomplete"}],
                "score": 60,
            }
        else:
            self.blackboard["quality_gate"] = {
                "verdict": "WARN",
                "features": [{"name": "f", "status": "YES"}],
                "score": 100,
            }


def _register(cls, name: str):
    PhaseRegistry.register(name, cls)


def _reset_counts():
    _RecordingPhase._counts = {}
    _AlwaysFailQualityGate._counts = {}
    _ThenPassQualityGate._counts = {}


def test_quality_gate_fail_jumps_back_to_verification():
    _reset_counts()
    _register(_RecordingPhase, "Verification")
    _register(_RecordingPhase, "Documentation")
    _register(_ThenPassQualityGate, "QualityGate")  # fails once then passes

    events = []
    HookRegistry.on(Events.PHASE_RETRY, lambda ev, **kw: events.append(kw))

    phases = ["Verification", "Documentation", "QualityGate"]
    bb = Blackboard()
    bb["directory"] = ""
    Pipeline(bb).run(phases)

    assert _RecordingPhase._counts["_RecordingPhase"] == 4  # Verification+Doc ran twice
    assert _ThenPassQualityGate._counts["_ThenPassQualityGate"] == 2
    assert len(events) == 1
    assert events[0]["phase"] == "Verification"
    assert bb["quality_gate_loops"] == 1


def test_quality_gate_loop_caps_at_max():
    _reset_counts()
    _register(_RecordingPhase, "Verification")
    _register(_RecordingPhase, "Documentation")
    _register(_AlwaysFailQualityGate, "QualityGate")  # always FAIL

    events = []
    HookRegistry.on(Events.PHASE_RETRY, lambda ev, **kw: events.append(kw))

    phases = ["Verification", "Documentation", "QualityGate"]
    bb = Blackboard()
    bb["directory"] = ""
    Pipeline(bb).run(phases)

    # Verification+Doc run once extra per loop, capped at MAX_QUALITY_GATE_LOOPS
    assert len(events) == Pipeline.MAX_QUALITY_GATE_LOOPS
    assert bb["quality_gate_loops"] == Pipeline.MAX_QUALITY_GATE_LOOPS
    # QualityGate ran 1 + loops times, Verification/Doc ran loops times
    assert _AlwaysFailQualityGate._counts["_AlwaysFailQualityGate"] == \
        Pipeline.MAX_QUALITY_GATE_LOOPS + 1


def test_quality_gate_fail_exhausted_marks_failure():
    """FAIL after loops exhausted → quality_gate_failed flag set.

    The pipeline_complete event is emitted by the runner (devforge/server/
    runner.py) — the pipeline itself must NOT fire it (B1: no double 🎉).
    """
    _reset_counts()
    _register(_RecordingPhase, "Verification")
    _register(_RecordingPhase, "Documentation")
    _register(_AlwaysFailQualityGate, "QualityGate")  # always FAIL

    completes = []
    HookRegistry.on(Events.PIPELINE_COMPLETE,
                    lambda ev, **kw: completes.append(kw))

    phases = ["Verification", "Documentation", "QualityGate"]
    bb = Blackboard()
    bb["directory"] = ""
    Pipeline(bb).run(phases)

    assert bb["quality_gate_failed"] is True
    # B1: pipeline must not double-fire — the runner's emit() is canonical.
    assert completes == []


def test_quality_gate_pass_no_loop():
    _reset_counts()
    _register(_RecordingPhase, "Verification")
    _register(_RecordingPhase, "Documentation")
    _register(_AlwaysPassQualityGate, "QualityGate")

    phases = ["Verification", "Documentation", "QualityGate"]
    bb = Blackboard()
    bb["directory"] = ""

    events = []
    HookRegistry.on(Events.PHASE_RETRY, lambda ev, **kw: events.append(kw))

    Pipeline(bb).run(phases)

    assert _RecordingPhase._counts["_RecordingPhase"] == 2  # ran once each
    assert len(events) == 0
    assert bb["quality_gate_loops"] == 0


def test_quality_gate_warn_with_missing_jumps_back():
    """WARN 含未达标项 → 同样回跳 Verification（不能"不通过却说完成"）。"""
    _reset_counts()
    _register(_RecordingPhase, "Verification")
    _register(_RecordingPhase, "Documentation")
    _register(_ThenCleanWarnQualityGate, "QualityGate")

    phases = ["Verification", "Documentation", "QualityGate"]
    bb = Blackboard()
    bb["directory"] = ""
    Pipeline(bb).run(phases)

    # 第一轮 WARN 含 PARTIAL → 回跳；再跑一次后全 YES → 放行
    assert _RecordingPhase._counts["_RecordingPhase"] == 4
    assert bb["quality_gate_loops"] == 1
    assert bb.get("quality_gate_failed") is None


def test_quality_gate_warn_missing_exhausted_marks_failure():
    """WARN 含未达标项耗尽重试 → 同样带失败标记交付。"""
    _reset_counts()
    _register(_RecordingPhase, "Verification")
    _register(_RecordingPhase, "Documentation")
    _register(_AlwaysFailQualityGate, "QualityGate")

    phases = ["Verification", "Documentation", "QualityGate"]
    bb = Blackboard()
    bb["directory"] = ""
    Pipeline(bb).run(phases)

    assert bb["quality_gate_failed"] is True


def test_start_from_keeps_venv_and_restores_checkpoint(tmp_path, monkeypatch):
    """重跑（start_from）必须指向项目已有 .venv（否则退回系统 Python，
    生成代码的依赖全部丢失），且从前一阶段 checkpoint 恢复 blackboard。"""
    import json as _json
    import codegen.application.chat_chain as chain_mod
    from codegen.infrastructure.tools.registry import init as rt_init, runtime

    monkeypatch.setattr(chain_mod, "_OUT_DIR", tmp_path)
    proj = tmp_path / "demo task_DevForge_20260802_ab12cd"
    (proj / ".venv" / "Scripts").mkdir(parents=True)
    (proj / ".venv" / "Scripts" / "python.exe").write_text("")
    # 工件在新布局的 .devforge/ 下（不污染交付目录）
    (proj / ".devforge").mkdir()
    (proj / ".devforge" / "checkpoint_RequirementsDiscussion.json").write_text(
        _json.dumps({"requirements": {"project_name": "x"}}), encoding="utf-8")
    (proj / ".devforge" / "checkpoint.json").write_text(
        _json.dumps({"phase": "RequirementsDiscussion"}), encoding="utf-8")
    (proj / ".devforge" / "task.txt").write_text("demo task", encoding="utf-8")

    try:
        chain = chain_mod.ChatChain(
            config={"pipeline": ["RequirementsDiscussion", "Design"]},
            task_prompt="demo task", run_id="ab12cd", start_from="Design")
        assert runtime().ctx.venv_dir == str(proj / ".venv")   # 复用已有 venv
        assert chain.blackboard["directory"] == str(proj)      # 不新建目录
        assert chain.blackboard.get("requirements") == {"project_name": "x"}
    finally:
        rt_init("", "")   # 复位 contextvar，避免污染其他测试


def test_user_feedback_triggers_design_rollback():
    """运行中追加需求 → 阶段边界消费队列 → 回退 Design + 记录需求历史。"""
    from serving.application.ws_manager import push_feedback
    bb = Blackboard()
    bb["_run_id"] = "r1"
    bb["requirements"] = {"project_name": "x"}
    pipe = Pipeline(bb)
    push_feedback("r1", "加一个导出功能")
    target = pipe._check_user_feedback(
        ["RequirementsDiscussion", "Design", "Coding"])
    assert target == ("Design", 1)                       # 回退 Design
    assert bb["user_feedback"] == ["加一个导出功能"]
    assert len(bb["requirements_history"]) == 1
    assert pipe._check_user_feedback(["Design", "Coding"]) is None   # 已消费


def test_git_commit_created(tmp_path):
    """每轮运行产物入 git（可回滚/对比），.gitignore 排除 venv。"""
    import subprocess as sp
    bb = Blackboard()
    bb["directory"] = str(tmp_path)
    bb["task_prompt"] = "测试任务"
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    Pipeline(bb)._git_commit()
    r = sp.run(["git", "-C", str(tmp_path), "log", "--oneline"],
               capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    assert "测试任务" in r.stdout
    assert (tmp_path / ".gitignore").exists()
    assert ".venv/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_token_budget_warns_once(monkeypatch):
    """超预算只警告一次（不刷屏）。"""
    HookRegistry.clear()
    events = []
    HookRegistry.on("token_warning", lambda ev, **kw: events.append(kw))
    bb = Blackboard()
    bb["usage_log"] = {"coder": {"prompt_tokens": 600000, "calls": 5}}
    pipe = Pipeline(bb)
    monkeypatch.setattr(
        "codegen.application.pipeline.load_pipeline_config",
        lambda: {"llm": {"token_budget": 500000}})
    pipe._check_token_budget()
    pipe._check_token_budget()
    assert len(events) == 1
    assert events[0]["budget"] == 500000


def test_artifact_path_falls_back_to_legacy_layout(tmp_path):
    """旧布局（工件在根目录）的目录仍可被读取（重跑兼容）。"""
    import codegen.application.chat_chain as chain_mod
    # 新布局优先
    new_dir = tmp_path / "new"
    (new_dir / ".devforge").mkdir(parents=True)
    (new_dir / ".devforge" / "checkpoint.json").write_text("{}")
    assert chain_mod.artifact_path(str(new_dir), "checkpoint.json") \
        == str(new_dir / ".devforge" / "checkpoint.json")
    # 旧布局：根目录存在时读取根目录
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    (old_dir / "checkpoint.json").write_text("{}")
    assert chain_mod.artifact_path(str(old_dir), "checkpoint.json") \
        == str(old_dir / "checkpoint.json")
    # 都不存在 → 新路径（写入目标）
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert chain_mod.artifact_path(str(fresh), "checkpoint.json") \
        == str(fresh / ".devforge" / "checkpoint.json")


def test_memory_write_runs_in_background(tmp_path, monkeypatch):
    """记忆写入后台线程执行 —— 阶段边界不再被 ChromaDB upsert 阻塞。

    若同步执行，_save_checkpoint 会在 release.wait 上阻塞 5s 才返回；
    异步则立即返回。
    """
    import threading
    from memory.infrastructure.chroma_store import MemoryStore
    from codegen.application.pipeline import Pipeline

    bb = Blackboard()
    bb["directory"] = str(tmp_path)
    bb["requirements"] = {"project_name": "X"}

    release = threading.Event()
    entered = threading.Event()

    def fake_write(self, project, phase, bb):
        entered.set()
        release.wait(5)          # 模拟慢速 ChromaDB upsert

    # 本测试只关心线程语义（后台执行）—— 不碰真实 ChromaDB：
    # 构造器 stub 掉，避免 ONNX embedding 首次初始化数秒吃掉 2s 断言窗口；
    # 换一把新锁，避免排队在本文件先前测试遗留的真实记忆写线程后面
    #（那些线程持模块级锁做真实写入，串行排队可达数十秒 → 偶发超时）
    import codegen.application.pipeline as pipe_mod
    monkeypatch.setattr(MemoryStore, "__init__", lambda self, chroma_dir="": None)
    monkeypatch.setattr(pipe_mod, "_memory_lock", threading.Lock())
    monkeypatch.setattr(MemoryStore, "write_phase", fake_write)
    Pipeline(bb)._save_checkpoint("Design")
    # _save_checkpoint 不等待记忆写入（后台线程仍在跑）
    assert not entered.is_set() or release.is_set() is False
    assert entered.wait(2)       # 后台线程确实启动了
    release.set()


def test_quality_gate_loops_configurable(monkeypatch):
    """quality_gate_max_loops 配置控制重修次数（默认 3 次回跳）。"""
    _reset_counts()
    _register(_RecordingPhase, "Verification")
    _register(_RecordingPhase, "Documentation")
    _register(_AlwaysFailQualityGate, "QualityGate")
    # 配置只允许 1 次回跳 → 共 2 次质检
    import codegen.application.pipeline as pipe_mod
    monkeypatch.setattr(
        pipe_mod, "load_pipeline_config",
        lambda *a, **k: {"quality_gate_max_loops": 1})

    phases = ["Verification", "Documentation", "QualityGate"]
    bb = Blackboard()
    bb["directory"] = ""
    Pipeline(bb).run(phases)

    assert bb["quality_gate_loops"] == 1
    assert bb["quality_gate_failed"] is True


class _EvidenceWarnQualityGate(_RecordingPhase):
    """Fake QG: WARN 但未达标项全是证据门槛（测试失败）→ 不回跳。"""

    def run(self):
        super().run()
        self.blackboard["quality_gate"] = {
            "verdict": "WARN",
            "features": [{"name": "自动化测试通过", "status": "NO",
                          "notes": "pytest error", "source": "evidence"}],
            "score": 80,
        }


def test_evidence_gate_items_do_not_trigger_loop():
    """测试失败等证据项（source=evidence）不触发 Verification 回跳 ——
    修复者修不了测试框架问题，回跳只会白烧验证轮次。"""
    _reset_counts()
    _register(_RecordingPhase, "Verification")
    _register(_RecordingPhase, "Documentation")
    _register(_EvidenceWarnQualityGate, "QualityGate")

    phases = ["Verification", "Documentation", "QualityGate"]
    bb = Blackboard()
    bb["directory"] = ""
    Pipeline(bb).run(phases)

    assert _RecordingPhase._counts["_RecordingPhase"] == 2   # 只跑一遍
    assert bb["quality_gate_loops"] == 0


def test_token_budget_stop_raises(monkeypatch):
    """token_budget_stop 硬上限 → 超了终止运行。"""
    import codegen.application.pipeline as pipe_mod
    bb = Blackboard()
    bb["directory"] = ""
    bb["usage_log"] = {"x": {"prompt_tokens": 700000, "completion_tokens": 0,
                             "calls": 1}}
    p = Pipeline(bb)
    monkeypatch.setattr(pipe_mod, "load_pipeline_config",
                        lambda: {"llm": {"token_budget": 500000,
                                         "token_budget_stop": 600000}})
    import pytest
    from codegen.domain.exceptions import PipelineError
    with pytest.raises(PipelineError, match="硬上限"):
        p._check_token_budget()


def test_feedback_targets_iterate_when_code_exists(monkeypatch, tmp_path):
    """审阅修复：目录已有代码 → 追加需求回退 Iterate（增量）而非 Design。"""
    from serving.application import ws_manager as conn
    phases = ["RequirementsDiscussion", "Design", "Coding",
              "Verification", "Documentation", "QualityGate", "Iterate"]
    bb = Blackboard()
    bb["_run_id"] = "fb-test"
    bb["directory"] = str(tmp_path)
    (tmp_path / "main.py").write_text("print(1)")
    monkeypatch.setattr(conn, "drain_feedback", lambda rid: ["加导出功能"])
    p = Pipeline(bb)
    target = p._check_user_feedback(phases)
    assert target == ("Iterate", 6)

    # 无代码 → 回退 Design
    bb2 = Blackboard()
    bb2["_run_id"] = "fb-test2"
    bb2["directory"] = str(tmp_path)
    (tmp_path / "main.py").unlink()
    monkeypatch.setattr(conn, "drain_feedback", lambda rid: ["换平台"])
    target2 = Pipeline(bb2)._check_user_feedback(phases)
    assert target2 == ("Design", 1)
