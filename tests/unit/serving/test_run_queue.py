"""Test B4 任务队列（P0-1/P0-2 修复）— FIFO、状态流转、启动失败兜底。

P0-1 背景：init_run("starting") 之后 enqueue_or_run 把 run 自己算作活跃
run → 所有 /api/run 永远入队永不启动。修复后状态机为
starting → queued（有活跃 run）→ running（线程启动）→ complete/error。
"""
import time

import pytest

from serving.application import run_queue as runner, ws_manager as connection


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    connection._active_runs.clear()
    connection._ws_connections.clear()
    runner._pending.clear()
    runner._started.clear()
    # 防 enqueue_or_run 拉起的 worker 守护线程跨测试弹出队列项：
    # 测试直接驱动 _worker_tick_once
    monkeypatch.setattr(runner, "_queue_worker", lambda: None)
    yield


def _stub_run_pipeline(monkeypatch, calls):
    def fake(*args, **kwargs):
        calls.append((args, kwargs))
    monkeypatch.setattr(runner, "run_pipeline", fake)
    return fake


def test_enqueue_when_run_active(monkeypatch):
    """有活跃 run → 新 run 入队（status=queued），不启动线程。"""
    calls = []
    _stub_run_pipeline(monkeypatch, calls)
    connection.init_run("runner-a")
    connection.set_run_status("runner-a", "running")

    q = runner.enqueue_or_run("runner-b", "task-b")

    assert q == {"queued": True, "position": 1}
    assert connection.get_run("runner-b")["status"] == "queued"
    assert not calls                                    # 没被直接启动


def test_starts_directly_when_idle(monkeypatch):
    """无活跃 run → 直接启动线程，不入队。"""
    calls = []
    _stub_run_pipeline(monkeypatch, calls)
    connection.init_run("runner-a")
    # 按真实生命周期走合法迁移（starting→running→complete）——
    # Phase 6 起状态机在仓储层校验非法迁移
    connection.set_run_status("runner-a", "running")
    connection.set_run_status("runner-a", "complete")   # 已完成的 run 不算活跃

    q = runner.enqueue_or_run("runner-c", "task-c")

    assert q == {"queued": False, "position": 0}
    deadline = time.time() + 5
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    assert calls and calls[0][0][0] == "runner-c"       # run_pipeline(run_id, …)
    assert connection.get_run("runner-c")["status"] == "starting"


def test_fifo_positions(monkeypatch):
    """活跃 run 存在时连续入队两个 → 位置 1、2（FIFO 顺序）。"""
    _stub_run_pipeline(monkeypatch, [])
    connection.init_run("runner-a")
    connection.set_run_status("runner-a", "running")

    assert runner.enqueue_or_run("runner-b", "t")["position"] == 1
    assert runner.enqueue_or_run("runner-c", "t")["position"] == 2
    assert [item["run_id"] for item in runner._pending] \
        == ["runner-b", "runner-c"]


def test_worker_skips_when_active(monkeypatch):
    """worker 出队前检查活跃 run：活跃时不 pop。"""
    _stub_run_pipeline(monkeypatch, [])
    connection.init_run("runner-a")
    connection.set_run_status("runner-a", "running")
    runner._pending.append({"run_id": "runner-b", "task": "t", "kwargs": {}})

    runner._worker_tick_once()          # 手动触发一次轮询逻辑

    assert len(runner._pending) == 1    # 没被 pop


def test_construction_failure_marks_error(monkeypatch):
    """P0-2：ChatChain 构造失败（start_from 无历史）→ fail_run 置 error，
    不永久卡 starting 堵死队列。"""
    class _Boom:
        def __init__(self, *a, **k):
            raise ChatChainError("No previous run found")

    monkeypatch.setattr(runner, "ChatChain", _Boom)
    connection.init_run("runner-d")

    from codegen.domain.exceptions import ChatChainError
    with pytest.raises(ChatChainError):
        runner.run_pipeline("runner-d", "task-d")

    entry = connection.get_run("runner-d")
    assert entry["status"] == "error"
    assert "No previous run found" in entry["error"]


def test_stale_starting_not_active(monkeypatch):
    """starting 超过宽限期（启动线程已死）→ 不视为活跃，不堵死队列。"""
    connection.init_run("runner-e")
    connection._active_runs["runner-e"]["started_at"] = time.time() - 60

    assert runner._has_active_run() is False

    connection.init_run("runner-f")     # 刚 init（新鲜）→ 活跃
    assert runner._has_active_run() is True


def test_queue_status_reflects_state():
    """queue_status：排队中给位置；已启动 started=True。"""
    connection.init_run("runner-g")
    runner._pending.append({"run_id": "runner-g", "task": "t", "kwargs": {}})
    assert runner.queue_status("runner-g") == {"position": 1, "started": False}

    runner._started.add("runner-g")
    assert runner.queue_status("runner-g") == {"position": 0, "started": True}
