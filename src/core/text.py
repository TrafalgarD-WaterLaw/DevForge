"""纯文本处理 —— 宽松 JSON 解析（LLM 输出的容错入口）。

从原 devforge/agents/agent.py 抽取（，现 src/codegen/domain/agent.py）：
无副作用纯函数，
同样的输入永远得到同样的输出。
"""

def strip_code_fence(text: str) -> str | None:
    """剥掉 ```json … ``` 代码围栏；无围栏返回原文（供 parse_llm_output 宽松解析）。"""
    for marker in ("```json", "```JSON", "```"):
        if text.startswith(marker):
            body = text[len(marker):]
            if body.endswith("```"):
                return body[:-3].strip()
            return body.strip()
    return text

def extract_json_object(text: str) -> str | None:
    """提取文本中第一个"平衡"的 JSON 对象（容忍前导说明/尾随总结）。

    仅当前导说明以冒号结尾（"issues:"）或为 ```json 围栏时才接受 ——
    正文中部夹带的普通 {…} 代码片段（如 ```python 围栏）不应被误当成
    JSON。
    """
    start = text.find("{")
    if start < 0:
        return None
    prefix = text[:start].strip()
    if prefix:
        low = prefix.lower()
        if "```" in prefix and "```json" not in low:
            return None                       # 非 json 代码围栏 → 不提取
        if not (prefix.endswith(":") or low.endswith("```json")):
            return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None

def parse_llm_output(text: str) -> dict:
    """把 LLM 文本输出解析为 dict（宽松三级回退）。

    1. 原文合法 JSON → 直接解析；
    2. 剥代码围栏后合法 → 解析；
    3. 提取平衡 JSON 对象 → 解析（容忍前导说明/尾随总结）；
    4. 全部失败 → {"message": 原文}（纯文本输出视为 message，行为与
       JSON 输出一致 —— 文档/讨论类角色的兜底契约）。
    """
    import json as _json

    s = text.strip()
    if not s:
        return {"message": ""}
    for candidate in (s, strip_code_fence(s), extract_json_object(s)):
        if candidate is None:
            continue
        try:
            data = _json.loads(candidate)
        except (_json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
        return {"value": data}
    return {"message": s}
