"""MemoryStore — persistent cross-project knowledge via ChromaDB.

Two isolated collections:

    memories_phases    — Phase-level summaries (CTO retrieval)
    memories_functions — Function-level source code (Coder retrieval)
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

from memory.domain.extract import extract_function_entries, extract_phase_entry
from memory.interfaces.prompt_formatter import format_function_memories, format_memories  # noqa: F401
from memory.domain.models import MemoryEntry  # noqa: F401

_log = logging.getLogger(__name__)

MEMORY_DIR_NAME = ".memory"

# Collection names
COL_PHASES = "memories_phases"
COL_FUNCTIONS = "memories_functions"
COL_FIXES = "memories_fixes"

# Shared PersistentClient per directory — multiple runs opening the same
# ChromaDB path concurrently would contend on the SQLite lock.
_client_cache: dict[str, chromadb.PersistentClient] = {}
_client_lock = threading.Lock()
# Beyond this many open ChromaDB clients, drop the whole cache — each entry
# holds file handles + sqlite connections that would otherwise leak forever.
_CLIENT_CACHE_MAX = 64

def _open_client(path: str) -> chromadb.PersistentClient:
    with _client_lock:
        if path not in _client_cache:
            if len(_client_cache) >= _CLIENT_CACHE_MAX:
                _client_cache.clear()
            _client_cache[path] = chromadb.PersistentClient(path=path)
        return _client_cache[path]

# ═══════════════════════════════════════════════════════════
# MemoryStore
# ═══════════════════════════════════════════════════════════

class MemoryStore:
    """Persistent memory backed by ChromaDB — two isolated collections.

    Phases collection: design decisions, requirements, bug history.
    Functions collection: verified source code, tagged by function name.
    """

    _PHASES = frozenset({"RequirementsDiscussion", "Design", "Coding",
                          "Verification", "QualityGate"})

    def __init__(self, *, chroma_dir: str = ""):
        # 全局记忆库设计 —— 所有项目共享一份（跨项目检索是功能需求），
        # 此前的 directory 参数从未被使用，删除。测试可传 chroma_dir 隔离。
        if chroma_dir:
            target = Path(chroma_dir)
        else:
            from core.config import _project_root
            target = _project_root() / MEMORY_DIR_NAME / "chroma"
        target.mkdir(parents=True, exist_ok=True)
        self._client = _open_client(str(target))
        try:
            # Attempt once — the old code re-invoked the identical call in
            # the except branch, silently swallowing the real failure.
            self._embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        except Exception as exc:
            _log.warning(
                "ChromaDB default embedding init failed (%s: %s) — "
                "falling back to the client's default", type(exc).__name__, exc)
            self._embedding_fn = None
        self._col_phases = self._client.get_or_create_collection(
            name=COL_PHASES,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self._col_functions = self._client.get_or_create_collection(
            name=COL_FUNCTIONS,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        # M1: 修复模式（错误签名 → 修复对照），只存验证过的修复
        self._col_fixes = self._client.get_or_create_collection(
            name=COL_FIXES,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def _collection(self, phase: str):
        if phase == "Function":
            return self._col_functions
        if phase == "FixPattern":
            return self._col_fixes
        return self._col_phases

    # ── Write ──────────────────────────────────────────

    @staticmethod
    def _fingerprint(entry: MemoryEntry) -> str:
        """内容指纹：summary+tags+detail 前缀 —— 跨任务内容去重的依据。"""
        import hashlib
        blob = f"{entry.summary}|{'|'.join(entry.tags)}|{entry.detail[:300]}"
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    def _write_deduped(self, col, entry: MemoryEntry) -> bool:
        """内容级去重：同内容已存在于其它 key → 跳过（返回 False）。

        同 key（同任务重跑）→ 正常 upsert 刷新时间戳 —— 指纹命中自己
        不算重复。
        """
        fp = self._fingerprint(entry)
        try:
            hits = col.get(where={"fingerprint": fp}, limit=1)
        except Exception:
            hits = None
        if hits and hits.get("ids"):
            if hits["ids"][0] != entry.id:
                print(f"  [Memory] skip dup {entry.summary[:60]} "
                      f"(已存在于 {hits['ids'][0]})", flush=True)
                return False
        entry.metadata_fingerprint = fp
        return True

    def write(self, entry: MemoryEntry):
        """Upsert a memory entry — routed to the correct collection.

        M4: 容量上限 + 惰性淘汰 —— 超限时删最旧条目（记忆只进不出的
        长期膨胀问题）。
        """
        col = self._collection(entry.phase)
        label = "fn" if entry.phase == "Function" else entry.phase
        if not self._write_deduped(col, entry):
            return
        print(f"  [Memory] +{label} {entry.summary[:80]}", flush=True)
        col.upsert(
            ids=[entry.id],
            # detail 前缀进嵌入向量：函数代码体/需求 JSON 参与语义检索。
            # 此前只嵌 summary+tags，中文项目的函数记忆召回近似随机
            documents=[f"{entry.summary}\n{' '.join(entry.tags)}\n{entry.detail[:300]}"],
            metadatas=[{
                "project": entry.project,
                "phase": entry.phase,
                "tags": "|".join(entry.tags),
                "summary": entry.summary[:200],
                "detail": entry.detail[:8000],
                "timestamp": entry.timestamp,
                "fingerprint": entry.metadata_fingerprint,
            }],
        )
        self._evict_if_over_limit(col)

    def _evict_if_over_limit(self, col):
        """超容量时按时间戳删最旧（默认 阶段 500 / 函数 2000 / 修复 300，
        可配置）。"""
        try:
            from core.config import load_pipeline_config
            mem_cfg = load_pipeline_config().get("memory", {}) or {}
            max_phases = int(mem_cfg.get("max_phases", 500))
            max_functions = int(mem_cfg.get("max_functions", 2000))
            max_fixes = int(mem_cfg.get("max_fixes", 300))
        except Exception:
            max_phases, max_functions, max_fixes = 500, 2000, 300
        if col is self._col_phases:
            limit = max_phases
        elif col is self._col_fixes:
            limit = max_fixes
        else:
            limit = max_functions
        count = col.count()
        if count <= limit:
            return
        try:
            r = col.get(limit=count)
            rows = sorted(
                zip(r["ids"], r["metadatas"]),
                key=lambda t: float(t[1].get("timestamp", 0) or 0))
            evict = [i for i, _ in rows[:count - limit]]
            if evict:
                col.delete(ids=evict)
                print(f"  [Memory] 淘汰 {len(evict)} 条最旧记忆（超上限 {limit}）",
                      flush=True)
        except Exception:
            _log.exception("Memory eviction failed")

    def write_fix_pattern(self, entry: MemoryEntry):
        """写入修复模式（M1，phase="FixPattern" → fixes collection）。

        调用方保证"验证过的修复"（修复后测试通过）。"""
        if entry.phase != "FixPattern":
            entry.phase = "FixPattern"
        self.write(entry)

    def recall_fix_patterns(self, query: str, *, n: int = 2) -> list[dict]:
        """按错误签名召回历史修复模式（fixer 注入用）。"""
        return self._recall(self._col_fixes, query, n=n)

    def write_phase(self, project: str, phase: str, blackboard):
        """Extract a memory from *blackboard* after *phase* completes.
        Phase entries → phases collection.
        Function entries → functions collection.

        M8: Verification 的函数记忆写在第一轮（可能早于 fixer 修复/回跳），
        QualityGate 阶段用最终代码状态重写 —— 相同 id upsert 覆盖中间态，
        记忆库里留下的永远是交付时的实现。"""
        entry = extract_phase_entry(project, phase, blackboard)
        if entry:
            self.write(entry)
        if phase in ("Verification", "QualityGate"):
            for fn_entry in extract_function_entries(project, blackboard):
                self.write(fn_entry)

    def clear(self) -> int:
        """清空全部记忆，返回删除条数。不可恢复。"""
        total = 0
        for col in (self._col_phases, self._col_functions, self._col_fixes):
            try:
                ids = col.get(limit=100000)["ids"]
                if ids:
                    col.delete(ids=ids)
                    total += len(ids)
            except Exception:
                _log.exception("Memory clear failed")
        print(f"  [Memory] cleared {total} entries", flush=True)
        return total

    def delete_project(self, project: str) -> int:
        """删除某项目全部记忆（全部 collection）。

        质检最终未通过时清库 —— 失败的编码/审查经验不应留在记忆里
        （recall 侧虽已排除未完成项目，但写入侧也不能留垃圾）。
        """
        total = 0
        for col in (self._col_phases, self._col_functions, self._col_fixes):
            try:
                res = col.delete(where={"project": project})
                if isinstance(res, list):
                    total += len(res)
            except Exception:
                _log.exception("delete_project failed for %s", project)
        return total

    def _completed_projects(self) -> set[str]:
        """有 QualityGate 记忆的项目 = 完整交付过。"""
        try:
            r = self._col_phases.get(
                where={"phase": "QualityGate"}, limit=10000)
        except Exception:
            return set()
        return {m.get("project", "") for m in r.get("metadatas", [])}

    # ── Recall ─────────────────────────────────────────

    def recall_phases(self, query: str, *, project: str = "",
                      n: int = 5) -> list[dict]:
        """Search phase-level memories — for CTO design context."""
        return self._recall(self._col_phases, query, project=project, n=n)

    def recall_functions(self, query: str, *, project: str = "",
                         n: int = 5) -> list[dict]:
        """Search function-level memories — for Coder implementations."""
        return self._recall(self._col_functions, query, project=project, n=n)

    def _recall(self, col, query: str, *, project: str = "",
                n: int = 5) -> list[dict]:
        """Two-tier retrieval: keyword grep  + vector semantic .

        M2: 未完成项目的记忆被排除 —— 某项目从没有 QualityGate 记忆
        （= 从未完整交付）时，其早期阶段记忆（需求/设计）是"半途而废"
        的经验，不进入检索池。写入不足 1 小时的新条目豁免（当前运行
        自己的早期阶段记忆不被误伤）。
        """
        seen: set[str] = set()
        entries: list[dict] = []
        keywords = _extract_query_keywords(query)
        import re as _re
        cjk_blocks = _re.findall(r'[一-鿿]{2,}', query)

        completed = self._completed_projects()
        fresh_cutoff = time.time() - 3600

        where = {"project": project} if project else None
        try:
            count = col.count()
            if count == 0:
                return []
            vec_results = col.query(
                query_texts=[query],
                where=where,
                n_results=min(n * 3, max(count, 1)),
            )
            for i, mid in enumerate((vec_results.get("ids") or [[]])[0] or []):
                if mid in seen:
                    continue
                seen.add(mid)
                meta = (vec_results.get("metadatas") or [[]])[0][i] if vec_results.get("metadatas") else {}
                if col is self._col_phases:
                    mproj = meta.get("project", "")
                    mts = float(meta.get("timestamp", 0) or 0)
                    if mproj not in completed and mts < fresh_cutoff:
                        continue   # 未完成项目的旧记忆 — 排除
                dist = (vec_results.get("distances") or [[]])[0][i] if vec_results.get("distances") else 1.0
                kw_hit = _kw_hit(keywords, cjk_blocks, meta.get("tags", ""))
                sem_close = dist < 0.8
                exact = kw_hit and sem_close
                entries.append(_format_entry(mid, meta, dist, query, exact_match=exact))
        except Exception:
            _log.exception("Memory recall failed for: %s", query)

        entries.sort(key=lambda e: e["score"], reverse=True)
        top = entries[:n]
        if top:
            hits = " | ".join(
                f"{e['summary'][:50]} ({e['score']:.1f})" for e in top[:3]
            )
            print(f"  [Memory] recall '{query[:60]}' -> {len(top)} hits: {hits}", flush=True)
        return top

# ═══════════════════════════════════════════════════════════
# Retrieval helpers
# ═══════════════════════════════════════════════════════════

def _format_entry(mid: str, meta: dict, distance: float,
                  query: str, *, exact_match: bool) -> dict:
    ts = float(meta.get("timestamp", 0))
    return {
        "id": mid,
        "project": meta.get("project", ""),
        "phase": meta.get("phase", ""),
        "summary": meta.get("summary", ""),
        "detail": meta.get("detail", ""),
        "tags": (meta.get("tags", "")).split("|"),
        "score": _score(distance, ts, exact_match=exact_match),
    }

def _score(distance: float, timestamp: float,
           *, exact_match: bool = False) -> float:
    base = 3.0 if exact_match else 1.0 / (1.0 + distance)
    if timestamp > 0:
        weeks = max(0, (time.time() - timestamp) / (86400 * 7))
        base *= max(0.3, 0.95 ** weeks)
    return base

def _extract_query_keywords(query: str) -> list[str]:
    import re
    camel = re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', query)
    snake = re.findall(r'\b[a-z]+(?:_[a-z]+)+\b', query)
    single = re.findall(r'\b[a-z_]+\b', query.lower())
    # 中文查询：连续 CJK 块整块作关键词（tags 里 core_features 等是中文；
    # 子串命中由 _kw_hit 的 2 字公共子串兜底，见下）
    cjk = re.findall(r'[一-鿿]{2,}', query)
    stop = {"the", "and", "that", "with", "from", "this", "what", "how",
            "when", "where", "which", "does", "been", "have", "has", "had",
            "was", "were", "will", "would", "could", "should", "for", "are",
            "not", "but", "all", "can", "now", "new", "any", "its", "who",
            "did", "let", "set", "get", "put", "run", "use", "see"}
    keywords = camel + snake + cjk \
        + [w for w in single if len(w) >= 3 and w not in stop]
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result[:5]

def _kw_hit(keywords: list[str], cjk_blocks: list[str], tags: str) -> bool:
    """Keywords against the tag blob — English exact, Chinese by shared
    2-char substring (no tokenizer in the dependency budget)."""
    if any(kw in tags for kw in keywords):
        return True
    # 中文无分词器：查询块与 tag 有公共 2 字子串即视为命中。
    # "统计" ∈ "设计一个命令行字数统计工具" ∩ "统计行数" → 命中
    for block in cjk_blocks:
        bigrams = {block[i:i + 2] for i in range(len(block) - 1)}
        if any(b in tags for b in bigrams):
            return True
    return False
