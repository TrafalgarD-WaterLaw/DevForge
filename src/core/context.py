"""运行上下文 —— 每个流水线线程携带自己的 run id。

从 serving.application.ws_manager 抽取：纯 contextvars
包装，无任何业务依赖，属于 core 通用工具。并发 run 各自的事件
路由到各自的 WebSocket 靠它区分。
"""
import contextvars

_current_run_var: contextvars.ContextVar[str] = (
    contextvars.ContextVar("current_run_id", default=""))

def set_current_run(run_id: str) -> None:
    """绑定当前线程的活动 run id（每个 run 启动时调用一次）。"""
    _current_run_var.set(run_id)

def get_current_run() -> str:
    """返回当前线程绑定的 run id（无则 ""）。"""
    return _current_run_var.get()
