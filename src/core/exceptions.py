"""项目异常基类 —— 所有业务域异常继承 BaseAppError。

会把各模块的 `raise RuntimeError(...)` 逐一替换为域异常，
并禁止裸抛/裸吞 Exception（当前各域已有自己的异常类时可直接继承）。
"""

class BaseAppError(Exception):
    """DevForge 应用异常基类。

    用于区分"预期的业务失败"（可恢复、可展示给用户）与
    "意外 bug"（裸 Exception，必须修代码）。
    """
