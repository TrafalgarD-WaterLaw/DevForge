"""RequirementsDiscussion — PM always outputs JSON.  Zero prompt switching."""

from core.config import load_pipeline_config
from core.events import HookRegistry
from codegen.domain.phase import Phase
from codegen.domain.registry import register_phase
from serving.application.ws_manager import ask_choice, has_ws

# 默认提问数上限（模块导入时的默认值；实际值每次惰性读取，
# 改配置即时生效，不用重启）
MAX_QUESTIONS = 7

def _max_questions() -> int:
    """惰性读取 pm.max_questions —— 之前模块级导入时读一次，改配置
    要重启才生效。"""
    return int(load_pipeline_config().get("pm", {}).get("max_questions", MAX_QUESTIONS))

def _pm_extract(pm_output) -> tuple:
    """Leniently extract (question, summary, message) from PM output.

    DeepSeek's json_mode output for the PM is sometimes structurally off
    (message as dict, missing keys).  We only require the *question* or
    *summary* object to be well-formed to continue — a malformed message
    degrades to a default instead of aborting the conversation.
    """
    if not isinstance(pm_output, dict):
        return (None, None, "")
    q = pm_output.get("question")
    if (
        not isinstance(q, dict)
        or "text" not in q
        or (not isinstance(q.get("options"), list))
    ):
        q = None
    s = pm_output.get("summary")
    if not isinstance(s, dict) or not s:
        s = None
    m = pm_output.get("message")
    if not isinstance(m, str):
        m = ""
    return (q, s, m)

@register_phase
class RequirementsDiscussion(Phase):
    """PM always outputs JSON with message + question + summary.  No prompt switching."""

    def run(self):
        run_id = self.blackboard["_run_id"]
        pm = self.agent("product_manager")
        pm_output = self._collect_requirements(pm, run_id)
        self._store_requirements(pm_output)

    def _collect_requirements(self, pm, run_id: str) -> dict:
        """初始生成 + 提问循环/headless 直出，返回最终 PM 输出。"""
        pm_output = pm.react(
            self.prompt("product_manager", max_questions=_max_questions()),
            json_mode=True,
        )
        if not has_ws(run_id):
            pm_output = self._headless_summary(pm)
        else:
            pm_output = self._ask_questions_loop(pm, run_id, pm_output)
        _, s, _ = _pm_extract(pm_output)
        if s is None:
            pm_output = pm.react(
                "All questions answered. Output your final summary as JSON.",
                json_mode=True,
            )
        return pm_output

    def _headless_summary(self, pm) -> dict:
        """无前端 WebSocket（benchmark/headless）：跳过提问循环，
        不浪费 PM 提问轮次。审阅修复：提示语强调完整需求 ——
        无澄清环节，summary 质量全押这一次生成。"""
        print("  [PM] 无前端连接 — 跳过提问，直接生成 summary", flush=True)
        from core.config import load_sys_message
        return pm.react(
            load_sys_message("pm_headless_summary"),
            json_mode=True,
        )

    def _ask_questions_loop(self, pm, run_id: str, pm_output: dict) -> dict:
        """提问循环：question → ask_choice → 回答回喂 PM，直到 summary。"""
        asked = 0
        while True:
            q, s, msg = _pm_extract(pm_output)
            if s is not None:
                return pm_output
            if q is None:
                print(f"  [PM] malformed output: {str(pm_output)[:120]}", flush=True)
                if asked == 0:
                    asked += 1
                    from core.config import load_sys_message
                    pm_output = pm.react(
                        load_sys_message("pm_malformed_retry"),
                        json_mode=True,
                    )
                    continue
                return pm_output
            asked += 1
            if asked > _max_questions():
                print(
                    f"  [PM] exceeded {MAX_QUESTIONS} questions — forcing summary",
                    flush=True,
                )
                return pm_output
            answer = self._ask_one_question(run_id, q, msg)
            pm_output = pm.react(answer, json_mode=True)

    def _ask_one_question(self, run_id: str, q: dict, msg: str) -> str:
        question_text = f"{msg}\n\n{q['text']}" if msg else q["text"]
        result = ask_choice(
            run_id, question_text, q["options"], q.get("allow_multiple", False)
        )
        selected = result.get("selected", [])
        custom = result.get("custom", "")
        if custom:
            selected.append(f"Other: {custom}")
        return ", ".join(selected) if selected else "(no answer)"

    def _store_requirements(self, pm_output: dict) -> None:
        """summary 提取 + 兜底 + 归一化 + 落黑板 + 事件 + task_description。"""
        _, s, _ = _pm_extract(pm_output)
        req = s or {}
        if not req:
            print(
                "  [PM] summary missing after retries — using task prompt", flush=True
            )
            req = self._fallback_requirements()
        req.setdefault(
            "project_name",
            self.blackboard.get("task_prompt", "").strip()[:30] or "Project",
        )
        req.setdefault("product_type", "?")
        req.setdefault("language", "Python")
        if not isinstance(req.get("core_features"), list):
            req["core_features"] = []
        # 需求膨胀护栏：用户任务通常 2-6 项功能 —— PM 超量扩展会让
        # 质检拿"需求外功能"当标准回跳修复（白烧 token）。超 6 项裁剪
        # 并告警（保留前 6 项，最贴近用户原话的顺序）。
        if len(req["core_features"]) > 6:
            print(f"  [PM] 需求膨胀：{len(req['core_features'])} 项 "
                  f"core_features 超上限 — 裁剪为前 6 项", flush=True)
            req["core_features"] = req["core_features"][:6]
        self.blackboard["requirements"] = req
        HookRegistry.trigger("requirements_submitted", data=req)
        self.blackboard["task_description"] = self._describe(req)

    def _fallback_requirements(self) -> dict:
        return {
            "project_name": self.blackboard.get("task_prompt", "").strip()[:30]
            or "Project",
            "product_type": "?",
            "language": "Python",
            "core_features": [],
        }

    def _describe(self, req: dict) -> str:
        """Build a human-readable description from whatever fields the PM returned."""
        parts: list[str] = []
        for key, val in req.items():
            if isinstance(val, list):
                parts.append(f"{key}: " + ", ".join(val))
            elif val:
                parts.append(f"{key}: {val}")
        return "\n".join(parts)
