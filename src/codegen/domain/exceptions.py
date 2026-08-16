"""codegen 域异常 —— 继承项目基类 BaseAppError（异常体系）。"""
from core.exceptions import BaseAppError

class PipelineError(BaseAppError):
    """流水线运行期业务失败（预算超限、回跳目标缺失等）。"""

class PhaseExecutionError(PipelineError):
    """阶段执行失败且重试耗尽。"""

class ChatChainError(BaseAppError):
    """ChatChain 装配失败（start_from 无历史项目、目录非法等）。"""
