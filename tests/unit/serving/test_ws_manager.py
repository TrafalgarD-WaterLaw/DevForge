"""Test server connection manager — B9: event seq/cap/expiry; B10: project skip."""
import asyncio
import time

from serving.application.ws_manager import (
    _MAX_EVENTS,
    _active_runs,
    _ws_connections,
    emit,
    get_run,
    has_ws,
    init_run,
    register_ws,
)


def test_emit_adds_monotonic_seq():
    run_id = "b9-seq"
    init_run(run_id)
    for i in range(5):
        emit(run_id, {"event": "x", "n": i})
    events = _active_runs[run_id]["events"]
    assert [e["seq"] for e in events] == [0, 1, 2, 3, 4]
    assert [e["n"] for e in events] == [0, 1, 2, 3, 4]


def test_emit_merges_consecutive_llm_delta():
    """连续 llm_delta 在缓冲里合并为一条 —— 一次长流式上千条 delta
    不再把 phase_start/里程碑等关键事件挤出 2000 条缓冲。"""
    run_id = "b9-merge"
    init_run(run_id)
    emit(run_id, {"event": "llm_delta", "agent": "cto", "delta": "a"})
    emit(run_id, {"event": "llm_delta", "agent": "cto", "delta": "b"})
    emit(run_id, {"event": "phase_start", "phase": "Coding"})
    emit(run_id, {"event": "llm_delta", "agent": "cto", "delta": "c"})   # 跨事件不合并
    emit(run_id, {"event": "llm_delta", "agent": "cpo", "delta": "x"})   # 不同 agent 不合并
    events = _active_runs[run_id]["events"]
    assert events[0]["delta"] == "ab"          # 同 agent 连续 → 累积
    assert events[1]["event"] == "phase_start" # 关键事件保住了
    assert events[2]["delta"] == "c"
    assert events[3]["delta"] == "x"


def test_emit_caps_event_history():
    run_id = "b9-cap"
    init_run(run_id)
    for i in range(_MAX_EVENTS + 10):
        emit(run_id, {"event": "x", "i": i})
    events = _active_runs[run_id]["events"]
    assert len(events) == _MAX_EVENTS
    # seq 单调递增，裁剪不掉序号 — 前端据此去重
    assert events[0]["seq"] == 10
    assert events[-1]["seq"] == _MAX_EVENTS + 9


def test_emit_skips_storing_for_expired_runs():
    run_id = "b9-expired"
    init_run(run_id)
    entry = _active_runs[run_id]
    entry["status"] = "complete"
    entry["started_at"] = time.time() - 7200  # 2h 前完成
    emit(run_id, {"event": "x"})
    assert _active_runs[run_id]["events"] == []


def test_get_run_prunes_expired_entries():
    run_id = "b9-prune"
    init_run(run_id)
    _active_runs[run_id]["status"] = "error"
    _active_runs[run_id]["started_at"] = time.time() - 7200
    assert get_run(run_id) is None  # 惰性清理

    live = "b9-live"
    init_run(live)
    assert get_run(live) is not None


def test_has_ws():
    run_id = "b9-ws"
    init_run(run_id)
    try:
        assert has_ws(run_id) is False
        register_ws(run_id, object())
        assert has_ws(run_id) is True
    finally:
        _ws_connections.pop(run_id, None)


def test_list_projects_skips_venv_files(monkeypatch, tmp_path):
    """B10: list_projects 与 get_project 一样跳过 .venv/__pycache__。"""
    import serving.interfaces.project_routes as routes
    monkeypatch.setattr(routes, "WAREHOUSE", tmp_path)

    proj = tmp_path / "proj1"
    proj.mkdir()
    (proj / "main.py").write_text("print(1)", encoding="utf-8")
    venv = proj / ".venv" / "Lib" / "site-packages"
    venv.mkdir(parents=True)
    (venv / "torch.py").write_text("import torch", encoding="utf-8")
    (proj / "pkg" / "__pycache__" / "mod.cpython-311.pyc").parent.mkdir(parents=True)
    (proj / "pkg" / "__pycache__" / "mod.cpython-311.pyc").touch()

    res = asyncio.run(routes.list_projects())
    files = res["projects"][0]["files"]
    assert files == ["main.py"]


def test_list_projects_and_get_project_exclude_artifacts(monkeypatch, tmp_path):
    """B11: run_events.json / checkpoint*.json / task.txt 不进项目文件清单。"""
    import serving.interfaces.project_routes as routes
    monkeypatch.setattr(routes, "WAREHOUSE", tmp_path)

    proj = tmp_path / "proj2"
    proj.mkdir()
    (proj / "main.py").write_text("print(1)", encoding="utf-8")
    (proj / "run_events.json").write_text("{}", encoding="utf-8")
    (proj / "checkpoint.json").write_text("{}", encoding="utf-8")
    (proj / "checkpoint_Coding.json").write_text("{}", encoding="utf-8")
    (proj / "task.txt").write_text("build a counter", encoding="utf-8")

    res = asyncio.run(routes.list_projects())
    assert res["projects"][0]["files"] == ["main.py"]

    detail = asyncio.run(routes.get_project("proj2"))
    assert set(detail["files"].keys()) == {"main.py"}


# ── B3: ask_choice seq 校验 ────────────────────────────

def _start_fake_ws_loop():
    """asyncio loop in a daemon thread + recording fake WebSocket."""
    import threading

    sent = []

    class FakeWS:
        async def send_json(self, event):
            sent.append(event)

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    return loop, t, sent, FakeWS()


def _wait_until(predicate, timeout=5.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        time.sleep(0.01)
    return predicate()


def test_ask_choice_ignores_wrong_seq_reply():
    """B3: 旧问题的回复（seq 不匹配）被忽略；事件载荷携带 seq。"""
    import json as json_mod
    import threading

    from serving.application.ws_manager import (
        _reply_events, _reply_seq, ask_choice, has_ws, init_run,
        register_ws, submit_reply, unregister_ws)

    run_id = "b3-ask-seq"
    init_run(run_id)
    loop, t, sent, fake_ws = _start_fake_ws_loop()
    try:
        loop.call_soon_threadsafe(register_ws, run_id, fake_ws)
        assert _wait_until(lambda: has_ws(run_id))

        result = {}
        threading.Thread(
            target=lambda: result.setdefault(
                "value", ask_choice(run_id, "q?", ["a", "b"])),
            daemon=True).start()

        assert _wait_until(lambda: bool(sent)), "discuss_choice 事件未发出"
        assert sent[0]["qseq"] == 0  # 第一问 qseq=0（载荷用 qseq，避免与 emit 的 seq 重放去重冲突）

        # 错误 seq（99）→ 丢弃，ask_choice 继续等待
        submit_reply(run_id, json_mod.dumps(
            {"selected": ["WRONG"], "custom": ""}), seq=99)
        time.sleep(0.2)
        assert "value" not in result, "错误 seq 的回复不应唤醒 ask_choice"

        # 正确 seq（0）→ 唤醒并返回
        submit_reply(run_id, json_mod.dumps(
            {"selected": ["a"], "custom": ""}), seq=0)
        assert _wait_until(lambda: "value" in result)
        assert result["value"]["selected"] == ["a"]
    finally:
        loop.call_soon_threadsafe(unregister_ws, run_id)
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)
        _reply_events.pop(run_id, None)
        _reply_seq.pop(run_id, None)


def test_ask_choice_accepts_seqless_reply():
    """B3: 不带 seq 的回复（旧前端）向后兼容 — 照常接受。"""
    import json as json_mod
    import threading

    from serving.application.ws_manager import (
        _reply_events, _reply_seq, ask_choice, has_ws, init_run,
        register_ws, submit_reply, unregister_ws)

    run_id = "b3-ask-no-seq"
    init_run(run_id)
    loop, t, sent, fake_ws = _start_fake_ws_loop()
    try:
        loop.call_soon_threadsafe(register_ws, run_id, fake_ws)
        assert _wait_until(lambda: has_ws(run_id))

        result = {}
        threading.Thread(
            target=lambda: result.setdefault(
                "value", ask_choice(run_id, "q?", ["a", "b"])),
            daemon=True).start()

        assert _wait_until(lambda: bool(sent))
        submit_reply(run_id, json_mod.dumps(
            {"selected": ["b"], "custom": ""}))  # 无 seq 参数
        assert _wait_until(lambda: "value" in result)
        assert result["value"]["selected"] == ["b"]
    finally:
        loop.call_soon_threadsafe(unregister_ws, run_id)
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)
        _reply_events.pop(run_id, None)
        _reply_seq.pop(run_id, None)


def test_ask_choice_seq_increments_per_question():
    """B3: 每问一题 seq +1 — 第二次提问载荷 seq=1。"""
    import json as json_mod
    import threading

    from serving.application.ws_manager import (
        _reply_events, _reply_seq, ask_choice, has_ws, init_run,
        register_ws, submit_reply, unregister_ws)

    run_id = "b3-ask-incr"
    init_run(run_id)
    loop, t, sent, fake_ws = _start_fake_ws_loop()
    try:
        loop.call_soon_threadsafe(register_ws, run_id, fake_ws)
        assert _wait_until(lambda: has_ws(run_id))

        def _ask():
            return ask_choice(run_id, "q?", ["a"])

        for expected_seq in (0, 1):
            result = {}
            threading.Thread(
                target=lambda: result.setdefault("value", _ask()),
                daemon=True).start()
            assert _wait_until(lambda: len(sent) > expected_seq)
            assert sent[expected_seq]["qseq"] == expected_seq
            submit_reply(run_id, json_mod.dumps(
                {"selected": ["a"], "custom": ""}), seq=expected_seq)
            assert _wait_until(lambda: "value" in result)
    finally:
        loop.call_soon_threadsafe(unregister_ws, run_id)
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)
        _reply_events.pop(run_id, None)
        _reply_seq.pop(run_id, None)


def test_ask_approval_headless_auto_passes():
    """无 ws 时人工审阅自动通过（headless 不阻塞流程）。"""
    from serving.application.ws_manager import ask_approval
    assert ask_approval("nonexistent-run", {"files": ["a.py"], "diff": "x"}) is True


def test_feedback_queue_push_drain():
    """运行中用户消息队列：push 后 drain 一次消费完。"""
    from serving.application.ws_manager import drain_feedback, push_feedback
    push_feedback("r2", "msg1")
    push_feedback("r2", "msg2")
    assert drain_feedback("r2") == ["msg1", "msg2"]
    assert drain_feedback("r2") == []


def test_critical_events_survive_trim():
    """C4: 缓冲超限时先丢非关键事件，关键事件（phase/质检/审阅）保留。"""
    run_id = "c4-critical"
    init_run(run_id)
    for i in range(_MAX_EVENTS):
        emit(run_id, {"event": "tool_pre_use", "agent": "a", "tool": "read_file"})
    emit(run_id, {"event": "quality_gate", "data": {"verdict": "PASS"}})
    emit(run_id, {"event": "phase_start", "phase": "Coding"})
    events = _active_runs[run_id]["events"]
    assert len(events) == _MAX_EVENTS
    kinds = [e["event"] for e in events]
    assert "quality_gate" in kinds          # 关键事件在裁剪中活下来
    assert "phase_start" in kinds
    assert kinds.count("tool_pre_use") < _MAX_EVENTS  # 非关键被丢弃了一部分


def test_unregister_stale_ws_keeps_new_connection():
    """P1-3：断线重连时旧 handler 的 finally 不能注销新连接。"""
    from serving.application.ws_manager import unregister_ws

    class _W:
        pass

    w1, w2 = _W(), _W()
    run_id = "ws-race"
    _ws_connections[run_id] = w1
    register_ws(run_id, w2)              # 新连接覆盖旧连接

    unregister_ws(run_id, w1)            # 旧连接的 finally —— 应被忽略

    assert _ws_connections.get(run_id) is w2

    unregister_ws(run_id, w2)            # 当前连接注销 → 正常移除
    assert run_id not in _ws_connections


def test_seq_allocation_unique_under_threads():
    """P2-1：并发 emit 不产生重复 seq（前端按 seq 去重，重复 = 丢事件）。"""
    import threading
    from serving.application.ws_manager import emit

    run_id = "seq-race"
    init_run(run_id)

    def _emit(i):
        for _ in range(200):
            emit(run_id, {"event": "x", "i": i})

    threads = [threading.Thread(target=_emit, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seqs = [e["seq"] for e in _active_runs[run_id]["events"]]
    assert len(seqs) == len(set(seqs))   # 全部唯一（锁保证）
