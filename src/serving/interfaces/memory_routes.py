"""memory 路由 —— 记忆库管理。"""
import logging

from fastapi import APIRouter

_log = logging.getLogger(__name__)
router = APIRouter()

# ═══════════════════════════════════════════
# Memory management 
# ═══════════════════════════════════════════

def _memory_snapshot(col, limit: int = 8) -> list[dict]:
    """集合最近条目（按时间戳倒序）。"""
    try:
        r = col.get(limit=1000)
    except Exception:
        return []
    out = []
    for i, mid in enumerate(r["ids"]):
        m = r["metadatas"][i]
        out.append({
            "id": mid,
            "project": m.get("project", ""),
            "phase": m.get("phase", ""),
            "summary": m.get("summary", ""),
            "tags": (m.get("tags", "") or "").split("|"),
            "timestamp": m.get("timestamp", 0),
        })
    out.sort(key=lambda e: float(e["timestamp"] or 0), reverse=True)
    return out[:limit]

@router.get("/api/memory")
async def get_memory_overview():
    """记忆库概览：条数 + 最近条目（管理 UI）。"""
    from core.config import _project_root
    from memory.infrastructure.chroma_store import MemoryStore
    store = MemoryStore()
    return {
        "location": str(_project_root() / ".memory" / "chroma"),
        "phases": {"count": store._col_phases.count(),
                   "recent": _memory_snapshot(store._col_phases)},
        "functions": {"count": store._col_functions.count(),
                      "recent": _memory_snapshot(store._col_functions)},
    }

@router.post("/api/memory/clear")
async def clear_memory():
    """清空记忆库（不可恢复）。"""
    from memory.infrastructure.chroma_store import MemoryStore
    return {"cleared": MemoryStore().clear()}
