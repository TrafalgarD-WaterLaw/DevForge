"""Test Verification review validation — B4: valid output must NOT re-prompt."""
from unittest.mock import MagicMock

from codegen.domain.blackboard import Blackboard
from core.events import Events, HookRegistry

import codegen.application.phases.verification as verif_mod
from codegen.application.phases.verification import Verification


def _mk_reviewer(data, name="SecurityReviewer"):
    agent = MagicMock()
    agent.name = name
    agent.react.return_value = data
    return agent


def _run_review(monkeypatch, agent, data):
    bb = Blackboard()
    bb["directory"] = ""  # no project dir → no subprocess/reload
    monkeypatch.setattr(verif_mod, "parallel", lambda tasks: [(agent, data)])
    # keep the fixer out of the LLM loop; _run_tests skipped via monkeypatch
    monkeypatch.setattr(
        Verification, "agent",
        lambda self, key, *, tag="": MagicMock(name=tag or key))
    monkeypatch.setattr(Verification, "_run_tests", lambda self: (False, "ok"))
    Verification(bb).run()
    return bb


def test_valid_output_does_not_reprompt(monkeypatch):
    """B4: 合法输出（无 issues）→ 不再调用 react 做无谓的二次校验。"""
    HookRegistry.clear()
    data = {"issues": []}
    agent = _mk_reviewer(data)
    bb = _run_review(monkeypatch, agent, data)

    agent.react.assert_not_called()
    assert bb["review_SecurityReviewer"] == []


def test_valid_with_issues_no_reprompt(monkeypatch):
    """B4: 合法输出（有 issues）→ 不 re-prompt，直接消费（嵌套字段无 KeyError）。"""
    HookRegistry.clear()
    data = {"issues": [{"file": "a.py", "line": 1,
                        "severity": "HIGH", "description": "bug"}]}
    agent = _mk_reviewer(data)
    bb = _run_review(monkeypatch, agent, data)

    agent.react.assert_not_called()
    assert bb["review_SecurityReviewer"] == data["issues"]


def test_invalid_output_reprompts_then_discards(monkeypatch):
    """B4: 非法输出 → 重试一次；仍非法 → 丢弃（不作为 "no issues"）。"""
    HookRegistry.clear()
    bad = {"issues": [{"file": "a.py"}]}            # 缺 line/severity/description
    agent = _mk_reviewer(bad)
    agent.react.return_value = bad                  # 重试后仍非法
    bb = _run_review(monkeypatch, agent, bad)

    # validated_react 每轮: 初次 + 1 次重试（retries=1）；
    # discarded>0 → 不提前返回，第二轮 re-review 再各来一次（修复后重查）
    assert agent.react.call_count == 4
    assert "review_SecurityReviewer" not in bb


def test_review_tasks_use_json_mode(monkeypatch):
    """reviewer 并行任务必须 json_mode=True：首轮即输出 issues 数组 JSON，
    不再先给一段 {"message": ...} 白白多花一轮 schema 重试。"""
    HookRegistry.clear()
    captured = {}

    def fake_parallel(tasks):
        captured["tasks"] = tasks
        return []

    monkeypatch.setattr(verif_mod, "parallel", fake_parallel)
    bb = Blackboard()
    bb["directory"] = ""
    monkeypatch.setattr(Verification, "_run_tests", lambda self: (False, "ok"))
    Verification(bb).run()

    tasks = captured["tasks"]
    assert tasks, "reviewer tasks must be scheduled"
    assert all(len(t) == 3 and t[2] is True for t in tasks)


def test_all_reviews_discarded_not_silent_pass(monkeypatch):
    """审查输出全被丢弃 → 里程碑不得声称"审查通过，无问题"，
    且不提前返回 —— fixer 仍被调用（否则问题被静默吞掉）。"""
    import json as _json
    HookRegistry.clear()
    msgs = []

    def on_turn(ev, **kw):
        # 里程碑 content 是 JSON 串（ensure_ascii 默认转义中文），parse 后取 message
        raw = kw.get("content", "")
        try:
            msgs.append(_json.loads(raw).get("message", raw))
        except Exception:
            msgs.append(raw)

    HookRegistry.on(Events.CONVERSATION_TURN, on_turn)

    bad = {"message": "not an issues json"}         # 非法 + 重试后仍非法
    agent = _mk_reviewer(bad)
    monkeypatch.setattr(verif_mod, "parallel", lambda tasks: [(agent, bad)])
    agents: list = []
    monkeypatch.setattr(
        Verification, "agent",
        lambda self, key, *, tag="": agents.append(key) or MagicMock(name=tag or key))
    monkeypatch.setattr(Verification, "_run_tests", lambda self: (False, "ok"))

    bb = Blackboard()
    bb["directory"] = ""
    Verification(bb).run()

    assert not any("审查通过" in m for m in msgs), "非法输出不能被当成无问题"
    assert any("审查输出无效" in m for m in msgs)
    # discarded>0 → 不提前返回：两轮循环每轮都调 fixer 复核
    assert agents.count("fixer") == 2


def test_review_round_and_loop_payloads(monkeypatch):
    """每轮循环发 review_round(loop)；review_submitted 携带 loop 与 issues。

    前端据此：第 2 轮重置窗口 + 轮次分隔线；问题数显示在窗口徽标。
    """
    HookRegistry.clear()
    rounds, submitted = [], []
    HookRegistry.on("review_round",
                    lambda ev, **kw: rounds.append(kw.get("loop")))
    HookRegistry.on("review_submitted",
                    lambda ev, **kw: submitted.append(
                        (kw.get("agent"), len(kw.get("issues", [])),
                         kw.get("loop"))))

    good = {"issues": [{"file": "a.py", "line": 1,
                        "severity": "HIGH", "description": "x"}]}
    agent = _mk_reviewer(good)
    monkeypatch.setattr(verif_mod, "parallel", lambda tasks: [(agent, good)])
    monkeypatch.setattr(
        Verification, "agent",
        lambda self, key, *, tag="": MagicMock(name=tag or key))
    monkeypatch.setattr(Verification, "_run_tests", lambda self: (True, "fail"))

    bb = Blackboard()
    bb["directory"] = ""
    Verification(bb).run()

    # 两轮循环各发一次 review_round（1, 2）
    assert rounds == [1, 2]
    # review_submitted 带 loop 与 issues 数
    assert submitted, "review_submitted 必须触发"
    assert all(loop in (1, 2) for _, _, loop in submitted)
    assert submitted[0][1] == 1


def test_review_discarded_event(monkeypatch):
    """输出非法被丢弃 → review_discarded 事件（前端窗口标 ⚠️ 无效）。"""
    HookRegistry.clear()
    discarded = []
    HookRegistry.on("review_discarded",
                    lambda ev, **kw: discarded.append(kw.get("agent")))

    bad = {"message": "not an issues json"}
    agent = _mk_reviewer(bad)
    monkeypatch.setattr(verif_mod, "parallel", lambda tasks: [(agent, bad)])
    monkeypatch.setattr(
        Verification, "agent",
        lambda self, key, *, tag="": MagicMock(name=tag or key))
    monkeypatch.setattr(Verification, "_run_tests", lambda self: (True, "fail"))

    bb = Blackboard()
    bb["directory"] = ""
    Verification(bb).run()

    assert discarded == ["SecurityReviewer"] * 2   # 两轮各丢弃一次


def test_run_process_timeout_kills_child():
    """B3/B9: 超时后必须终止子进程并返回（不挂起、不抛未处理 OSError）。"""
    import subprocess
    import sys
    import time
    from codegen.domain.blackboard import Blackboard

    bb = Blackboard()
    bb["directory"] = ""
    verif = Verification(bb)
    start = time.monotonic()
    out, err, code = verif._run_process(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        ".", timeout=1)
    elapsed = time.monotonic() - start
    assert code is not None      # 进程被终止并回收，而非卡死
    assert code != 0             # 超时终止 → 非零退出码
    assert elapsed < 30          # 没有挂到子进程自己退出


def test_post_fix_tests_run_again(monkeypatch):
    """修复后再跑一次测试：最后一轮修复完要验证修复真的修好了。"""
    HookRegistry.clear()
    test_calls = []
    monkeypatch.setattr(Verification, "_run_tests",
                        lambda self: (test_calls.append(1), (False, "ok"))[1])

    good = {"issues": [{"file": "a.py", "line": 1,
                        "severity": "HIGH", "description": "bug"}]}
    agent = _mk_reviewer(good)
    monkeypatch.setattr(verif_mod, "parallel", lambda tasks: [(agent, good)])
    monkeypatch.setattr(
        Verification, "agent",
        lambda self, key, *, tag="": MagicMock(name=tag or key))

    bb = Blackboard()
    bb["directory"] = "C:/fake"
    monkeypatch.setattr(bb, "reload_codes", lambda d: None)
    monkeypatch.setattr(Verification, "_request_review", lambda self, d, c=None: None)
    Verification(bb).run()

    # 每轮：修复前 1 次 + 修复后 1 次 = 2 次 × 2 轮 = 4 次
    assert len(test_calls) == 4
