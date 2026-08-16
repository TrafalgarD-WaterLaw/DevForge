"""Test A2 增量迭代 — Iterate 阶段 + ChatChain project_dir 模式."""
import json

from codegen.domain.blackboard import Blackboard
from core.events import HookRegistry
from codegen.application.phases.iterate import Iterate


def test_iterate_phase_summarizes_changes(monkeypatch, tmp_path):
    """迭代：只改相关文件 → 里程碑含文件数与回归测试状态。"""
    HookRegistry.clear()
    msgs = []
    HookRegistry.on("conversation_turn",
                    lambda ev, **kw: msgs.append(kw.get("content", "")))

    bb = Blackboard()
    bb["directory"] = str(tmp_path)
    bb["task_prompt"] = "给报表加 CSV 导出"
    bb["requirements"] = {"core_features": ["报表"]}
    bb["codes"] = {"report.py": "def report(): ..."}
    (tmp_path / "report.py").write_text("def report(): ...", encoding="utf-8")

    phase = Iterate(bb)

    class _FakeEngineer:
        name = "iteration_engineer"
        def react(self, prompt, *, json_mode=False, stream=False):
            # 模拟修改：写盘新内容
            (tmp_path / "report.py").write_text(
                "def report(): ...\ndef export_csv(): ...", encoding="utf-8")
            return {"message": "done"}

    monkeypatch.setattr(phase, "agent", lambda key: _FakeEngineer())
    # iterate.py 在 run() 内部 import Verification —— patch 其来源模块
    class _FakeV:
        def __init__(self, blackboard):
            pass
        def _run_tests(self):
            return False, "ok"
    monkeypatch.setattr("codegen.application.phases.verification.Verification",
                        _FakeV)
    # 审阅门与文档同步（审阅修复新增）—— 单元测试不触达真实连接/LLM
    monkeypatch.setattr(phase, "_request_review", lambda changed: None)
    # iterate.py 函数内 import —— patch 其来源模块
    monkeypatch.setattr(
        "codegen.application.phases.documentation.Documentation",
        type("D", (), {"run": lambda self: None}))
    monkeypatch.setattr(
        "codegen.application.phases.coding.Coding",
        type("C", (), {"_auto_install": lambda self, d: None}))

    phase.run()

    summary = json.loads(msgs[-1])
    assert "修改 1 个文件" in summary["message"]
    assert "回归测试通过" in summary["message"]
    assert bb["iterate_changed"] == ["report.py"]


def test_chain_project_dir_reuses_directory(tmp_path, monkeypatch):
    """ChatChain(project_dir=...) 不新建目录、不覆盖 task.txt、
    加载最近完整 checkpoint、反馈写入 feedback.txt。"""
    import codegen.application.chat_chain as chain_mod
    monkeypatch.setattr(chain_mod, "_OUT_DIR", tmp_path)
    monkeypatch.setattr("codegen.infrastructure.tools.registry.init", lambda *a, **k: None)

    proj = tmp_path / "existing_project"
    (proj / ".devforge").mkdir(parents=True)
    (proj / ".devforge" / "task.txt").write_text("原始任务", encoding="utf-8")
    (proj / ".devforge" / "checkpoint_QualityGate.json").write_text(
        json.dumps({"requirements": {"project_name": "x"}}), encoding="utf-8")
    (proj / ".devforge" / "checkpoint_Design.json").write_text(
        json.dumps({"requirements": {"project_name": "older"}}), encoding="utf-8")

    chain = chain_mod.ChatChain(
        config={"pipeline": ["Iterate"]},
        task_prompt="加导出功能", run_id="it1",
        project_dir=str(proj))

    assert chain.blackboard["directory"] == str(proj)
    assert chain.blackboard.get("requirements") == {"project_name": "x"}
    assert (proj / ".devforge" / "task.txt").read_text(encoding="utf-8") \
        == "原始任务"                                  # 未被覆盖
    assert (proj / ".devforge" / "feedback.txt").read_text(encoding="utf-8") \
        == "加导出功能"


def test_chain_project_dir_missing_raises(tmp_path, monkeypatch):
    import codegen.application.chat_chain as chain_mod
    monkeypatch.setattr(chain_mod, "_OUT_DIR", tmp_path)
    import pytest
    from codegen.domain.exceptions import ChatChainError
    with pytest.raises(ChatChainError):
        chain_mod.ChatChain(config={"pipeline": ["Iterate"]},
                            task_prompt="x", run_id="it2",
                            project_dir=str(tmp_path / "nope"))
