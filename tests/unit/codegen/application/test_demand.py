"""Test RequirementsDiscussion malformed-output retry (bounded, no infinite loop)."""
import pytest

from codegen.domain.blackboard import Blackboard
from core.events import HookRegistry
from codegen.application.phases.requirements_discussion import RequirementsDiscussion


@pytest.fixture(autouse=True)
def clean_hooks():
    HookRegistry.clear()
    yield
    HookRegistry.clear()


class _FakePM:
    """Fake product_manager — returns queued outputs, records react prompts."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def react(self, prompt, *, json_mode=False):
        self.calls.append(prompt)
        return self.outputs.pop(0)


def _invalid():
    # Neither a valid 'question' nor a valid 'summary' — message only
    return {"message": "let me think..."}


def _valid_summary():
    return {
        "summary": {
            "project_name": "X", "product_type": "Y",
            "language": "Python", "core_features": [],
        }
    }


def test_malformed_output_retries_exactly_once_then_summary(monkeypatch):
    """PM 两次输出均畸形 → 只重试一次 → 继续并产出 summary（不无限循环）。"""
    pm = _FakePM([_invalid(), _invalid(), _valid_summary()])
    bb = Blackboard()
    bb["_run_id"] = "test-run"
    phase = RequirementsDiscussion(bb)
    monkeypatch.setattr(phase, "agent", lambda key: pm)
    # 有前端连接（has_ws=True）→ 走提问循环
    import codegen.application.phases.requirements_discussion as demand_mod
    monkeypatch.setattr(demand_mod, "has_ws", lambda rid: True)

    phase.run()

    # 2 次畸形输出（初始 + 1 次重试）+ 1 次最终 summary 调用
    assert len(pm.calls) == 3
    assert "Output valid JSON" in pm.calls[1]
    assert "final summary" in pm.calls[2]
    assert bb["requirements"]["project_name"] == "X"
    assert "X" in bb["task_description"]


def test_question_loop_caps_at_max(monkeypatch):
    """B9: PM 持续提问 → 硬上限 MAX_QUESTIONS → 强制 summary（不无限循环）。"""
    import codegen.application.phases.requirements_discussion as demand_mod

    def _question(i):
        return {
            "message": f"m{i}",
            "question": {"text": f"question {i}?", "options": ["a", "b"]},
        }

    pm = _FakePM([_question(i) for i in range(8)] + [_valid_summary()])
    bb = Blackboard()
    bb["_run_id"] = "cap-run"
    phase = RequirementsDiscussion(bb)
    monkeypatch.setattr(phase, "agent", lambda key: pm)
    monkeypatch.setattr(demand_mod, "has_ws", lambda rid: True)

    calls = {"n": 0}

    def _fake_ask(run_id, question, options, allow_multiple=False):
        calls["n"] += 1
        return {"selected": ["a"], "custom": ""}

    monkeypatch.setattr(demand_mod, "ask_choice", _fake_ask)

    phase.run()

    # 初始调用 + 7 次问答 + 1 次强制 summary = 9 次 react
    assert calls["n"] == demand_mod.MAX_QUESTIONS
    assert len(pm.calls) == demand_mod.MAX_QUESTIONS + 2
    assert "final summary" in pm.calls[-1]
    assert bb["requirements"]["project_name"] == "X"


def test_headless_skips_question_loop(monkeypatch):
    """无前端 WebSocket（benchmark/headless）→ 跳过提问循环，只产出 summary
    （B11：不浪费 PM 提问轮次）。审阅修复后 headless 分支有专门的
    "生成完整 summary" 调用。"""
    pm = _FakePM([_invalid(), _valid_summary()])
    bb = Blackboard()
    bb["_run_id"] = "headless-run"
    phase = RequirementsDiscussion(bb)
    monkeypatch.setattr(phase, "agent", lambda key: pm)
    import codegen.application.phases.requirements_discussion as demand_mod
    monkeypatch.setattr(demand_mod, "has_ws", lambda rid: False)

    phase.run()

    # 初始调用 + 1 次 headless 完整 summary 调用，不进提问循环
    assert len(pm.calls) == 2
    assert "MOST COMPLETE" in pm.calls[1]
    assert bb["requirements"]["project_name"] == "X"


def test_summary_normalized_when_fields_missing(monkeypatch):
    """P0-2: PM summary 缺 core_features 等字段 → 运行时归一化兜底，
    下游 Design 覆盖检查/质检不会静默拿不到 features。"""
    pm = _FakePM([_invalid(), {"summary": {"project_name": "X"}}])   # 初始无效 + headless 完整 summary
    bb = Blackboard()
    bb["_run_id"] = "norm-run"
    phase = RequirementsDiscussion(bb)
    monkeypatch.setattr(phase, "agent", lambda key: pm)
    import codegen.application.phases.requirements_discussion as demand_mod
    monkeypatch.setattr(demand_mod, "has_ws", lambda rid: False)

    phase.run()

    req = bb["requirements"]
    assert req["project_name"] == "X"        # 已有值保留
    assert req["core_features"] == []        # 缺失 → 兜底空列表
    assert req["language"] == "Python"       # 缺失 → 默认
    assert req["product_type"] == "?"
