"""run 路由 —— 任务提交/队列轮询/WebSocket/平台配置。"""
import json
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Form, WebSocket
from pydantic import BaseModel

from core.config import _project_root, load_phases_config
from serving.application.ws_manager import (
    get_run,
    push_feedback,
    register_ws,
    submit_reply,
    submit_review_decision,
    unregister_ws,
)

_log = logging.getLogger(__name__)
router = APIRouter()
WAREHOUSE = _project_root() / "WareHouse"

# ═══════════════════════════════════════════
# Pipeline
# ═══════════════════════════════════════════

class RunRequest(BaseModel):
    task: str
    pipeline: str = "default"
    name: str = ""
    start_from: str = ""
    project: str = ""

@router.post("/api/run")
async def run_task(
    body: Optional[RunRequest] = Body(None),
    task: Optional[str] = Form(None),
    start_from: Optional[str] = Form(None),
    project: Optional[str] = Form(None),
):
    t = (body and body.task) or task
    if not t:
        return {"error": "task is required"}

    sf = (body and body.start_from) or start_from or ""
    pipeline_name = (body and body.pipeline) or "default"
    # 增量迭代：project = 已有项目目录名（必须位于 WareHouse 内）
    project_dir = ""
    pname = (body and body.project) or project or ""
    if pname:
        pdir = (WAREHOUSE / pname).resolve()
        if not str(pdir).startswith(str(WAREHOUSE.resolve()) + os.sep):
            return {"error": "Access denied"}
        if not pdir.is_dir():
            return {"error": "Project not found"}
        project_dir = str(pdir)
    run_id = uuid.uuid4().hex[:8]
    # 有 run 在跑时入队（FIFO），worker 依次启动。
    # init_run 由 enqueue_or_run 负责 —— 先判活跃再 init，否则刚 init 的
    # "starting" 会让 run 把自己当成活跃者、永远入队
    from serving.application.run_queue import enqueue_or_run
    q = enqueue_or_run(
        run_id, t,
        start_from=sf, pipeline=pipeline_name, project_dir=project_dir)
    if q["queued"]:
        return {"run_id": run_id, "status": "queued",
                "queued": True, "position": q["position"]}
    return {"run_id": run_id, "status": "running"}

@router.get("/api/queue/{run_id}")
async def get_queue_status(run_id: str):
    """排队状态轮询：{position, started} —— 前端就绪后自动连接 ws。"""
    from serving.application.run_queue import queue_status
    return queue_status(run_id)

@router.get("/api/run/{run_id}")
async def get_run_status(run_id: str):
    run = get_run(run_id)
    if not run:
        return {"error": "Unknown run_id"}
    return {k: run[k] for k in ("status", "events", "project_dir", "error")}

# ═══════════════════════════════════════════
# WebSocket
# ═══════════════════════════════════════════

@router.websocket("/ws/{run_id}")
async def ws_events(ws: WebSocket, run_id: str):
    # 访问令牌鉴权：auth_token 非空时 ws 必须带 ?token=（ws 无 HTTP 头）
    from serving.interfaces.app import auth_token
    token = auth_token()
    if token and ws.query_params.get("token") != token:
        await ws.close(code=4401)
        return
    await ws.accept()
    run = get_run(run_id)
    if not run:
        await ws.send_json({"event": "error", "message": "Unknown run_id"})
        await ws.close()
        return

    register_ws(run_id, ws)
    # 重放标记：前端据此清空对话流，避免断线重连后事件整体重复
    try:
        await ws.send_json({"event": "replay_start"})
    except Exception:
        return
    # iterate a snapshot — the live run keeps appending to run["events"],
    # so a concurrent emit mid-replay must not shift our iteration window.
    for event in list(run["events"]):
        try:
            await ws.send_json(event)
        except Exception:
            return

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")
            if msg_type == "discuss_choice":
                reply = json.dumps({
                    "selected": data.get("selected", []),
                    "custom": data.get("custom", ""),
                })
                submit_reply(run_id, reply, seq=data.get("qseq"))
            elif msg_type == "user_message":
                # 运行中追加需求：入队，pipeline 阶段边界消费并回退 Design
                content = str(data.get("content", "")).strip()
                if content:
                    push_feedback(run_id, content)
            elif msg_type == "review_decision":
                submit_review_decision(
                    run_id, bool(data.get("approved", False)))
    except Exception:
        _log.debug("WebSocket closed for %s", run_id)
    finally:
        unregister_ws(run_id, ws)

# ═══════════════════════════════════════════
# Platform config
# ═══════════════════════════════════════════

@router.get("/api/config")
async def get_platform_config():
    """前端阶段面板路由表 —— 单一来源：phases.json。

    新增审查 lens 前端自动跟随，无需前端维护。
    """
    phases = load_phases_config()
    return {
        "stage_phases": {
            "Coding": {
                "allow": [],
                # coder 的 tag 是模块名（动态）→ 排除顺序收尾角色
                "exclude": [r for r in phases.get("Coding", {}).get("roles", [])
                            if r != "coder"],
            },
            # 整合联调子面板：编码完成后 integrator+tester 收进第二个面板
            "Integration": {
                "allow": [r for r in phases.get("Coding", {}).get("roles", [])
                          if r != "coder"],
                "exclude": [],
            },
            "Verification": {
                # fixer 修复过程留在面板里，避免其 work 行把面板顶上去
                "allow": [f"{l['name']}Reviewer"
                          for l in phases.get("Verification", {}).get("lenses", [])]
                         + ["fixer"],
                "exclude": [],
            },
            "Documentation": {
                "allow": list(phases.get("Documentation", {}).get("roles", [])),
                "exclude": [],
            },
        }
    }
