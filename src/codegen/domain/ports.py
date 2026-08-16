"""codegen 域端口接口 —— 依赖倒置的"协议"层。

domain 只依赖这些协议；具体实现（OpenAI SDK、文件系统、WebSocket 交互）
放在 infrastructure / serving，由装配容器注入。
先定义协议与类型标注；完成构造注入替换。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class LlmPort(Protocol):
    """LLM 调用协议 —— codegen.infrastructure.llm_client.LLMClient 实现。"""

    def call(self, messages: list[dict], tools: Any = None, *,
             json_mode: bool = False, max_tokens: int | None = None) -> dict:
        """非流式补全：返回 {content, tool_calls, finish_reason, usage}。"""
        ...

    def stream_call(self, messages: list[dict], tools: Any = None, *,
                    json_mode: bool = False, on_delta: Any = None,
                    max_tokens: int | None = None) -> str:
        """流式补全：逐块回调 on_delta(text)，返回完整文本。"""
        ...

class UserInteractionPort(Protocol):
    """阻塞式人机交互协议 —— serving.infrastructure.ws_interaction 实现。

    demand/verification/iterate 阶段只依赖此协议，不直接 import ws 模块
    （DI：装配容器注入；当前各阶段函数内 import 为过渡）。
    """

    def ask_choice(self, run_id: str, question: str, options: list[str],
                   allow_multiple: bool = False) -> dict:
        """提问并阻塞等待选择。"""
        ...

    def ask_approval(self, run_id: str, payload: dict) -> bool:
        """人工审阅请求并阻塞等待决策。"""
        ...

    def has_ws(self, run_id: str) -> bool:
        """当前 run 是否有在线前端连接。"""
        ...

class FeedbackPort(Protocol):
    """运行中用户反馈队列协议 —— ws_interaction.drain_feedback 实现。

    Pipeline 阶段边界消费用户追加需求；benchmark/headless 无 ws 时
    队列恒空（天然 no-op）。
    """

    def drain_feedback(self, run_id: str) -> list[str]:
        """消费并清空该 run 的待处理用户消息。"""
        ...
