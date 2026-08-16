"""Test Agent / Conversation / Phase core paths — mocked LLM, no HTTP."""
import pytest
from unittest.mock import patch, MagicMock

from codegen.domain.agent import Agent
from codegen.domain.blackboard import Blackboard
from codegen.application.patterns import converse


@pytest.fixture(autouse=True)
def _patch_llm():
    """Mock LLMClient.__init__ and init tool runtime."""
    from codegen.infrastructure.tools.registry import init
    def _fake_init(self):
        self._model_name = "deepseek-v4-flash"
        self.model = self._model_name
    with patch("codegen.infrastructure.llm_client.LLMClient.__init__", _fake_init):
        init()  # tool runtime must exist for Agent.react()
        yield


def _mk_call(agent, contents):
    """Replace ``agent._llm.call`` — *contents* is a list of ``{content, tool_calls}``."""
    if not isinstance(contents, list):
        contents = [contents]
    idx = [0]

    def call(messages=None, tools=None, **kwargs):
        i = min(idx[0], len(contents) - 1)
        result = contents[i]
        idx[0] += 1
        return result

    agent._llm.call = MagicMock(side_effect=call)


def _mk_agent(name="Test"):
    agent = Agent(name, Blackboard())
    _mk_call(agent, [{"content": '{"message": "stub reply"}', "tool_calls": None}])
    return agent


class TestAgent:
    def test_ask_returns_dict(self):
        agent = _mk_agent()
        result = agent.react("hello")
        assert isinstance(result, dict)

    def test_ask_appends_to_history(self):
        agent = _mk_agent()
        assert len(agent._messages) == 1  # system only
        agent.react("hello")
        assert len(agent._messages) >= 3  # system + user + assistant

    def test_receive_appends_formatted(self):
        agent = _mk_agent()
        agent.receive({"message": "Hello"})
        last = agent._messages[-1]
        assert last["role"] == "user"
        assert "Hello" in last["content"]

    def test_no_blackboard_ok(self):
        agent = Agent("X", blackboard=None)
        _mk_call(agent, [{"content": '{"message": "ok"}', "tool_calls": None}])
        result = agent.react("hi")
        assert result == {"message": "ok"}

    def test_malformed_tool_args_retries_with_feedback(self):
        """模型返回损坏的工具参数 → 不崩溃，反馈后重试，最终正常返回。"""
        agent = _mk_agent()
        agent._tool_schemas = [{"type": "function", "function": {
            "name": "write_file", "parameters": {"type": "object"}}}]
        bad = {"content": "", "tool_calls": [
            {"id": "tc1", "name": "write_file", "args": None}]}
        good = {"content": '{"message": "recovered"}', "tool_calls": None}
        _mk_call(agent, [bad, good])

        result = agent.react("write a file")

        assert result == {"message": "recovered"}
        assert agent._llm.call.call_count == 2  # 第一次损坏，第二次恢复
        # 历史中应包含反馈消息，且损坏的工具调用被 "{}" 占位回填
        feedback = [m for m in agent._messages
                    if m.get("role") == "user" and "invalid JSON" in str(m.get("content", ""))]
        assert feedback
        echo = [m for m in agent._messages
                if m.get("role") == "assistant" and m.get("tool_calls")]
        assert echo and echo[0]["tool_calls"][0]["function"]["arguments"] == "{}"

    def test_malformed_tool_args_never_executes_partial_round(self):
        """同一轮中混有损坏与正常调用时，本轮不执行任何工具。"""
        agent = _mk_agent()
        agent._run_tools = MagicMock()
        agent._tool_schemas = [{"type": "function", "function": {
            "name": "read_file", "parameters": {"type": "object"}}}]
        bad = {"content": "", "tool_calls": [
            {"id": "tc1", "name": "read_file", "args": None},
            {"id": "tc2", "name": "read_file", "args": {"path": "x.py"}}]}
        good = {"content": '{"message": "ok"}', "tool_calls": None}
        _mk_call(agent, [bad, good])

        result = agent.react("read")

        assert result == {"message": "ok"}
        # 工具执行不应发生（本轮全部跳过）
        assert agent._run_tools.call_count == 0


class TestConversation:
    def test_turn_alternation(self):
        a = _mk_agent("A")
        b = _mk_agent("B")

        _mk_call(a, [{"content": '{"message": "I propose a Web App."}', "tool_calls": None}])
        _mk_call(b, [{"content": '{"message": "I AGREE."}', "tool_calls": None}])

        result = converse(a, b, speaker_prompt="Pick a modality", max_turns=2)
        assert isinstance(result, dict)

    def test_stops_on_empty_response(self):
        a = _mk_agent("A")
        b = _mk_agent("B")
        _mk_call(a, [{"content": "", "tool_calls": None}])

        result = converse(a, b, speaker_prompt="Discuss", max_turns=2)
        assert result.get("_terminated")


class TestPhase:
    def test_design_prompt_renders_template(self):
        from codegen.domain.blackboard import Blackboard
        from codegen.application.phases.design import Design

        bb = Blackboard()
        bb["task_prompt"] = "test task"
        bb["task_description"] = "test desc"

        design = Design(bb)
        prompt = design.prompt("chief_technology_officer")
        assert "test task" in prompt


class TestToolExecution:
    def test_run_tools_parallel_reads_ordered_writes(self):
        """只读工具并行执行、写工具保序；结果按 tool_call_id 回填。"""
        agent = _mk_agent()
        from codegen.infrastructure.tools.registry import runtime
        rt = runtime()
        calls: list[str] = []
        real_execute = rt.execute

        def fake_execute(name, arguments):
            calls.append(name)
            return f"R:{name}"

        rt.execute = fake_execute
        try:
            agent._run_tools([
                {"id": "a", "name": "read_file", "args": {"filename": "x.py"}},
                {"id": "b", "name": "write_file",
                 "args": {"filename": "y.py", "content": "y"}},
                {"id": "c", "name": "read_file", "args": {"filename": "z.py"}},
            ])
        finally:
            rt.execute = real_execute

        tool_msgs = [m for m in agent._messages if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["a", "b", "c"]
        assert tool_msgs[0]["content"] == "R:read_file"
        # 写工具只执行一次、排在并行组之后（保序）
        assert calls.count("write_file") == 1
        assert calls[-1] == "write_file"
        assert calls.count("read_file") == 2


class TestContextManagement:
    def test_compact_drops_old_keeps_recent_and_system(self):
        agent = _mk_agent()
        agent._max_context_chars = 500
        agent._messages.append({"role": "user", "content": "旧问题" + "x" * 300})
        agent._messages.append({"role": "assistant", "content": "旧回答" + "y" * 300})
        agent._messages.append({"role": "user", "content": "新问题" + "z" * 200})
        agent._compact_if_needed()
        joined = "".join(m.get("content") or "" for m in agent._messages)
        assert "新问题" in joined                 # 最新消息保留
        assert "旧问题" not in joined            # 最旧被丢
        assert "已压缩" in joined                # 压缩说明
        assert agent._messages[0]["role"] == "system"   # 角色设定保留
        total = sum(len(m.get("content") or "") for m in agent._messages)
        assert total <= 500 + 200

    def test_compact_keeps_tool_pairs(self):
        """tool_calls 与其结果必须成对保留（拆散会 API 400）。"""
        agent = _mk_agent()
        agent._max_context_chars = 300
        agent._messages.append({"role": "user", "content": "q1" + "a" * 200})
        agent._messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "t1", "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"}}],
        })
        agent._messages.append({"role": "tool", "tool_call_id": "t1",
                                "content": "result" + "b" * 200})
        agent._messages.append({"role": "assistant", "content": "回答" + "c" * 100})
        agent._compact_if_needed()
        roles = [m["role"] for m in agent._messages]
        assert "tool" in roles                                  # 结果保留
        assert roles[roles.index("tool") - 1] == "assistant"    # 前面仍是 tool_calls
        assert any(m.get("content") is None for m in agent._messages)  # None content 不崩

    def test_no_compact_within_budget(self):
        agent = _mk_agent()
        agent._messages.append({"role": "user", "content": "短消息"})
        before = len(agent._messages)
        agent._compact_if_needed()
        assert len(agent._messages) == before


class TestStreaming:
    def test_react_stream_pushes_deltas_and_parses(self):
        """流式：stream_call 被调用，chunk 经 llm_delta 事件推送，完成解析。"""
        from core.events import HookRegistry
        HookRegistry.clear()
        events = []
        HookRegistry.on("llm_delta", lambda ev, **kw: events.append(kw.get("delta")))
        HookRegistry.on("llm_stream_end", lambda ev, **kw: events.append("__END__"))

        agent = _mk_agent()
        agent._tool_schemas = []          # 无工具 → 可流式
        deltas = ["架构", "方案"]

        def fake_stream(messages, on_delta=None, **kw):
            for d in deltas:
                on_delta(d)
            return "架构方案"

        agent._llm.stream_call = fake_stream
        result = agent.react("设计", stream=True)
        assert result == {"message": "架构方案"}
        assert events == ["架构", "方案", "__END__"]
        # 历史完整入 messages（后续阶段可引用）
        assert agent._messages[-1] == {"role": "assistant", "content": "架构方案"}

    def test_react_stream_not_used_for_json_or_tools(self):
        """json_mode 或带工具时不走流式（用普通 call）。"""
        agent = _mk_agent()
        agent._llm.stream_call = lambda **kw: (_ for _ in ()).throw(
            AssertionError("stream_call 不应被调用"))
        result = agent.react("回答", json_mode=True, stream=True)
        assert isinstance(result, dict)


class TestParseLenient:
    """宽松 JSON 解析：围栏/说明文字包裹的 JSON 也能解析（修复 reviewer
    带工具时输出常被包裹、全部判非法丢弃的根因）。"""

    def test_direct_json(self):
        from codegen.domain.agent import Agent
        assert Agent._parse('{"issues": []}') == {"issues": []}

    def test_markdown_fence(self):
        from codegen.domain.agent import Agent
        text = 'Here is my review:\n\n```json\n{"issues": [{"file": "a.py"}]}\n```'
        assert Agent._parse(text) == {"issues": [{"file": "a.py"}]}

    def test_trailing_summary_after_json(self):
        from codegen.domain.agent import Agent
        text = 'I found these issues: {"issues": []} Overall the code is good.'
        assert Agent._parse(text) == {"issues": []}

    def test_plain_text_falls_back_to_message(self):
        from codegen.domain.agent import Agent
        assert Agent._parse('No issues found.') == {"message": 'No issues found.'}

    def test_code_body_braces_not_mistaken_for_json(self):
        """正文中部夹带的 {…} 片段（长前导、无冒号引导）不得被当成 JSON。"""
        from codegen.domain.agent import Agent
        text = '```python\ndef f():\n    return {"a": 1}\n```'
        # 前导超 60 字符且无冒号/围栏引导 → 不提取，整段作为 message
        assert Agent._parse(text) == {"message": text}

    def test_short_colon_intro_allows_extract(self):
        from codegen.domain.agent import Agent
        text = ('Issues: {"issues": [{"file": "a.py", "line": 1, '
                '"severity": "HIGH", "description": "x"}]}')
        assert Agent._parse(text) == {"issues": [{"file": "a.py", "line": 1,
                                                   "severity": "HIGH",
                                                   "description": "x"}]}


class TestAgentDone:
    """agent_done：react 任何出口（普通/流式）都发一次完成事件。"""

    def test_react_emits_agent_done(self):
        from core.events import HookRegistry
        HookRegistry.clear()
        done = []
        HookRegistry.on("agent_done", lambda ev, **kw: done.append(kw.get("agent")))
        agent = _mk_agent()
        agent._llm.call = lambda *a, **kw: {
            "content": '{"message": "ok"}', "tool_calls": None,
            "finish_reason": "stop", "usage": None}
        agent.react("q")
        assert done == ["Test"]

    def test_react_stream_emits_agent_done(self):
        from core.events import HookRegistry
        HookRegistry.clear()
        done = []
        HookRegistry.on("agent_done", lambda ev, **kw: done.append(kw.get("agent")))
        agent = _mk_agent()
        agent._tool_schemas = []
        agent._llm.stream_call = (
            lambda messages, on_delta=None, **kw: (on_delta("hi"), "hi")[1])
        agent.react("设计", stream=True)
        assert done == ["Test"]


class TestToolRoundsExhaustion:
    """工具轮次耗尽 → 强制收尾（修复 reviewer 工具调用耗尽后返回
    _terminated、输出全判无效的系统性根因）。"""

    def _tool_agent(self, final_content):
        from unittest.mock import MagicMock
        agent = _mk_agent()
        agent._tool_schemas = [{"type": "function",
                                "function": {"name": "read_file",
                                             "parameters": {}}}]
        tc = [{"id": "t1", "name": "read_file", "args": {}}]
        rounds = [{"content": "", "tool_calls": tc,
                   "finish_reason": "tool_calls", "usage": None}] \
            * agent._max_tool_rounds
        rounds.append({"content": final_content, "tool_calls": None,
                       "finish_reason": "stop", "usage": None})
        agent._llm.call = MagicMock(side_effect=rounds)
        agent._run_tools = lambda tool_calls: None   # 不真执行工具
        return agent

    def test_exhausted_rounds_force_final_answer(self):
        agent = self._tool_agent('{"issues": []}')
        result = agent.react("审查", json_mode=True)
        assert result == {"issues": []}              # 不再是 _terminated
        calls = agent._llm.call.call_args_list
        assert len(calls) == agent._max_tool_rounds + 1  # N 轮工具 + 1 次强制收尾
        last = calls[-1]
        assert last.kwargs.get("tools") is None      # 收尾调用不带工具
        assert last.kwargs.get("json_mode") is True  # json_mode 真正生效

    def test_same_round_duplicate_tool_calls_executed_once(self):
        """同一轮内 (tool, args) 完全相同的调用只执行一次，结果复用。"""
        from core.events import HookRegistry
        HookRegistry.clear()
        pre_uses = []
        HookRegistry.on("tool_pre_use",
                        lambda ev, **kw: pre_uses.append(kw.get("tool")))
        agent = _mk_agent()
        agent._tool_schemas = [{"type": "function",
                                "function": {"name": "read_file",
                                             "parameters": {}}}]
        agent._llm.call = MagicMock(side_effect=[
            {"content": "", "tool_calls": [
                {"id": "t1", "name": "read_file", "args": {"path": "a.py"}},
                {"id": "t2", "name": "read_file", "args": {"path": "a.py"}},
                {"id": "t3", "name": "read_file", "args": {"path": "b.py"}},
            ], "finish_reason": "tool_calls", "usage": None},
            {"content": '{"issues": []}', "tool_calls": None,
             "finish_reason": "stop", "usage": None},
        ])
        result = agent.react("审查", json_mode=True)
        assert result == {"issues": []}
        # 同参数 t1/t2 只执行一次（pre_use 只对真实执行触发）
        assert pre_uses == ["read_file", "read_file"]
        # 历史里 t1/t2/t3 都拿到结果（API 要求每个 tool_call_id 都有结果）
        tool_msgs = [m for m in agent._messages if m["role"] == "tool"]
        assert {m["tool_call_id"] for m in tool_msgs} == {"t1", "t2", "t3"}
        # 复用的结果与执行的一致
        r1 = next(m for m in tool_msgs if m["tool_call_id"] == "t1")["content"]
        r2 = next(m for m in tool_msgs if m["tool_call_id"] == "t2")["content"]
        assert r1 == r2

    def test_json_truncation_rerequests_concise_even_with_tools(self):
        """带工具时 JSON 截断也走"整份重出"（续写拼接必非法）。"""
        from unittest.mock import MagicMock
        agent = _mk_agent()
        agent._tool_schemas = [{"type": "function",
                                "function": {"name": "read_file",
                                             "parameters": {}}}]
        agent._llm.call = MagicMock(side_effect=[
            {"content": '{"issues": [', "tool_calls": None,
             "finish_reason": "length", "usage": None},
            {"content": '{"issues": []}', "tool_calls": None,
             "finish_reason": "stop", "usage": None},
        ])
        result = agent.react("审查", json_mode=True)
        assert result == {"issues": []}
        calls = agent._llm.call.call_args_list
        assert len(calls) == 2
        assert calls[-1].kwargs.get("json_mode") is True
        assert calls[-1].kwargs.get("tools") is None

    def test_non_streaming_react_emits_typing(self):
        """非流式调用开始发 agent_typing（前端"思考中…"占位）。"""
        from core.events import HookRegistry
        HookRegistry.clear()
        events = []
        HookRegistry.on("agent_typing",
                        lambda ev, **kw: events.append(kw.get("agent")))
        agent = _mk_agent()
        agent.react("回答", json_mode=True)
        assert events == ["Test"]

    def test_streaming_react_no_typing(self):
        from core.events import HookRegistry
        HookRegistry.clear()
        events = []
        HookRegistry.on("agent_typing",
                        lambda ev, **kw: events.append(kw.get("agent")))
        agent = _mk_agent()
        agent._tool_schemas = []
        agent._llm.stream_call = (
            lambda messages, on_delta=None, **kw: (on_delta("hi"), "hi")[1])
        agent.react("设计", stream=True)
        assert events == []


class TestLLMResponseCache:
    """B3: 完全相同的请求复用缓存响应（不重复计费）。"""

    def test_identical_call_hits_cache(self):
        import time as _t
        from codegen.infrastructure.llm_client import _RESPONSE_CACHE
        _RESPONSE_CACHE.clear()
        from codegen.infrastructure.llm_client import LLMClient
        # __new__ 跳过真实 OpenAI 客户端构造，保留 call 的真实方法
        #（Phase 7 拆分后 call 委托 _build_params/_store_cache 等子方法，
        # 鸭子类型实例不再够用）
        client = LLMClient.__new__(LLMClient)
        client._model_name = "m"
        client._max_tokens = None
        client._disable_thinking = False
        client._client = type("S", (), {})()
        client._client.chat = type("Chat", (), {})()
        client._client.chat.completions = type("Completions", (), {})()
        calls = []

        def fake_create(**params):
            calls.append(params)
            class _Msg:
                content = "hello"
                tool_calls = None
            class _Ch:
                message = _Msg()
                finish_reason = "stop"
            class _Resp:
                choices = [_Ch()]
                usage = None
            return _Resp()

        client._client.chat.completions.create = fake_create
        LLMClient.call(client, [{"role": "user", "content": "q"}])
        LLMClient.call(client, [{"role": "user", "content": "q"}])
        assert len(calls) == 1             # 第二次命中缓存
        # 不同消息不命中
        LLMClient.call(client, [{"role": "user", "content": "q2"}])
        assert len(calls) == 2
        _RESPONSE_CACHE.clear()



def test_converse_agree_requires_own_line():
    """审阅修复：'I agree, but...' 不再提前终止；独占行 I AGREE 终止。"""
    from codegen.domain.agent import Agent
    from codegen.domain.blackboard import Blackboard
    from codegen.application.patterns import converse

    a = Agent("A", Blackboard())
    b = Agent("B", Blackboard())
    from unittest.mock import MagicMock
    # A 说 "I agree, but..." → 不终止；B 独占行 "I AGREE" → 终止
    a._llm.call = MagicMock(side_effect=[
        {"content": '{"message": "I agree, but the CLI needs a GUI too."}',
         "tool_calls": None, "finish_reason": "stop", "usage": None},
        {"content": '{"message": "\nI AGREE\n"}',
         "tool_calls": None, "finish_reason": "stop", "usage": None},
    ])
    b._llm.call = MagicMock(side_effect=[
        {"content": '{"message": "OK"}', "tool_calls": None,
         "finish_reason": "stop", "usage": None},
        {"content": '{"message": "\nI AGREE\n"}', "tool_calls": None,
         "finish_reason": "stop", "usage": None},
    ])
    # 第 1 轮：A 说 "I agree, but..." → 不终止，B 回应 "OK" → 轮到 A
    # 第 2 轮：A 独占行 I AGREE → 终止，result 是 A 的 I AGREE 消息
    result = converse(a, b, speaker_prompt="discuss", max_turns=4)
    assert "I AGREE" in result.get("message", "")
    # B 只回应过一次（"OK"）—— "I agree, but" 没有提前终止
    b_calls = [c for c in b._llm.call.call_args_list]
    assert len(b_calls) == 1


def test_parallel_empty_returns_empty():
    """P1-1：空任务列表短路返回 [] —— 此前 max_workers = min(0, …) = 0，
    ThreadPoolExecutor 抛 ValueError（编码阶段全模块产物缓存时 pending
    为空会走到这里）。"""
    from codegen.application.patterns import parallel
    assert parallel([]) == []


def test_stream_usage_recorded():
    """P1-8：流式输出也统计 token 用量（此前 _record_usage(None) 恒 0，
    设计讨论/文档阶段的成本与 token_budget 护栏完全看不到）。"""
    agent = Agent("Streamer", Blackboard())
    agent._llm.stream_call = MagicMock(return_value="streamed text")
    agent._llm.last_stream_usage = {"prompt_tokens": 100, "completion_tokens": 30}

    result = agent.react("hello", stream=True)

    assert result == {"message": "streamed text"}
    log = agent.blackboard["usage_log"]["Streamer"]
    assert log["prompt_tokens"] == 100
    assert log["completion_tokens"] == 30
