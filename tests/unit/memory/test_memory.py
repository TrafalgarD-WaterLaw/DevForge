"""Test memory system — write/recall/cross-project/dedup/extraction."""
import time
from pathlib import Path

import pytest

from memory.domain.extract import extract_function_entries, extract_phase_entry
from memory.interfaces.prompt_formatter import format_function_memories, format_memories
from memory.infrastructure.chroma_store import (
    COL_FUNCTIONS,
    COL_PHASES,
    MemoryEntry,
    MemoryStore,
    _extract_query_keywords,
    _score,
)
from codegen.domain.blackboard import Blackboard


@pytest.fixture
def store(tmp_path):
    """Fresh MemoryStore with isolated ChromaDB。

    uuid 后缀防跨 session 路径复用：pytest 的编号目录（pytest-N）在新进程
    从 0 重计数，同一测试两次会话会落到同一 tmp 路径 → 上次的 ChromaDB
    持久化残留污染本次（同会话连续多次全量跑时偶发失败的真因）。
    """
    import uuid
    chroma = tmp_path / "chroma" / uuid.uuid4().hex[:8]
    return MemoryStore(chroma_dir=str(chroma))


@pytest.fixture
def blackboard():
    bb = Blackboard()
    bb["modules"] = [
        {"name": "counter", "exports": [
            {"name": "count_stats", "signature": "(lines: list[str]) -> dict",
             "description": "Count lines, words, characters"},
        ], "files": ["counter.py"], "depends_on": []},
        {"name": "file_reader", "exports": [
            {"name": "read_lines", "signature": "(path: str) -> list[str]",
             "description": "Read file into list of lines"},
        ], "files": ["file_reader.py"], "depends_on": []},
    ]
    bb.codes = {
        "counter.py": "def count_stats(lines):\n    return {'lines': len(lines)}",
        "file_reader.py": "def read_lines(path):\n    with open(path) as f: return f.readlines()",
    }
    bb["review_SecurityReviewer"] = [
        {"file": "counter.py", "severity": "HIGH", "line": 3, "description": "split() bug"}
    ]
    bb["requirements"] = {"project_name": "test", "product_type": "CLI tool",
                          "language": "Python", "core_features": ["count", "display"]}
    bb["modality"] = "CLI tool"
    bb["language"] = "Python"
    return bb


# ── Scoring ───────────────────────────────────────

class TestScoring:
    def test_exact_match(self):
        assert _score(0.0, time.time(), exact_match=True) == pytest.approx(3.0, abs=0.1)

    def test_semantic(self):
        s = _score(0.5, time.time(), exact_match=False)
        assert s == pytest.approx(0.67, abs=0.05)

    def test_time_decay_reduces_old(self):
        now = time.time()
        fresh = _score(0.0, now, exact_match=True)
        old = _score(0.0, now - 60 * 86400, exact_match=True)
        assert old < fresh

    def test_time_decay_floor(self):
        now = time.time()
        ancient = _score(0.0, now - 400 * 86400, exact_match=True)
        assert ancient >= 0.85


# ── Keyword extraction ────────────────────────────

class TestKeywords:
    def test_snake_case(self):
        kw = _extract_query_keywords("count_stats file_reader.py")
        assert "count_stats" in kw

    def test_stop_words_removed(self):
        kw = _extract_query_keywords("the and that with for are")
        assert not kw

    def test_natural_language_no_keywords(self):
        # 纯英文停用词句 → 无关键词；中文块现在会被提取（特性，见下）
        assert _extract_query_keywords("the and that from this were") == []


# ── Format ────────────────────────────────────────

class TestFormat:
    def test_phase_level(self):
        result = format_memories([{"phase": "Design", "summary": "CLI tool, Python, 3 modules"}])
        assert "RELEVANT PAST EXPERIENCE" in result
        assert "Design" in result

    def test_length_limit(self):
        entries = [{"phase": "X", "summary": "A" * 200}] * 10
        result = format_memories(entries, max_chars=200)
        assert len(result) < 350

    def test_empty(self):
        assert format_memories([]) == ""

    def test_function_only_verified(self):
        entries = [
            {"phase": "Function", "summary": "ok", "detail": "def ok(): pass", "tags": ["verified"]},
            {"phase": "Function", "summary": "bad", "detail": "def bad(): pass", "tags": ["has-issues"]},
        ]
        result = format_function_memories(entries)
        assert "ok" in result
        assert "bad" not in result


# ── Two-collection isolation ──────────────────────

class TestCollections:
    def test_phase_entry_to_phases_col(self, store):
        store.write(MemoryEntry(id="d1", project="test", phase="Design",
            tags=["CLI"], summary="CLI design"))
        assert store._col_phases.count() == 1
        assert store._col_functions.count() == 0

    def test_function_entry_to_functions_col(self, store):
        store.write(MemoryEntry(id="f1", project="test", phase="Function",
            tags=["counter", "count_stats", "verified"],
            summary="count_stats(...) — text stats"))
        assert store._col_phases.count() == 0
        assert store._col_functions.count() == 1

    def test_phases_recall_isolated(self, store):
        store.write(MemoryEntry(id="d1", project="test", phase="Design",
            tags=["CLI"], summary="CLI tool design",
            timestamp=time.time()))
        store.write(MemoryEntry(id="f1", project="test", phase="Function",
            tags=["CLI", "main", "verified"], summary="main() CLI entry"))

        r = store.recall_phases("CLI", project="test")
        assert len(r) == 1
        assert r[0]["summary"] == "CLI tool design"

    def test_functions_recall_isolated(self, store):
        store.write(MemoryEntry(id="d1", project="test", phase="Design",
            tags=["CLI"], summary="CLI tool design",
            timestamp=time.time()))
        store.write(MemoryEntry(id="f1", project="test", phase="Function",
            tags=["CLI", "main", "verified"], summary="main() CLI entry"))

        r = store.recall_functions("CLI", project="test")
        assert len(r) == 1
        assert r[0]["summary"] == "main() CLI entry"


# ── MemoryStore ───────────────────────────────────

class TestMemoryStore:
    def test_write_and_recall(self, store):
        store.write(MemoryEntry(id="t1", project="test", phase="Design",
            tags=["CLI", "Python"], summary="CLI tool design",
            timestamp=time.time()))
        store.write(MemoryEntry(id="t2", project="test", phase="Verification",
            tags=["counter", "bug"], summary="split() bug",
            timestamp=time.time()))

        r = store.recall_phases("CLI design", project="test")
        assert len(r) >= 1
        assert r[0]["summary"] == "CLI tool design"

    def test_keyword_boost(self, store):
        store.write(MemoryEntry(id="fn-a", project="test", phase="Function",
            tags=["counter", "count_stats", "verified"], summary="count_stats OK"))
        store.write(MemoryEntry(id="fn-b", project="test", phase="Function",
            tags=["file_reader", "read_lines", "verified"], summary="read_lines OK"))

        r = store.recall_functions("count_stats", project="test")
        assert r[0]["score"] >= 2.9
        assert r[0]["summary"] == "count_stats OK"

    def test_cross_project(self, store):
        store.write(MemoryEntry(id="p1-Design", project="proj-a", phase="Design",
            tags=["CLI"], summary="CLI design",
            timestamp=time.time()))
        store.write(MemoryEntry(id="p2-Design", project="proj-b", phase="Design",
            tags=["Web"], summary="Web design",
            timestamp=time.time()))

        r = store.recall_phases("CLI tool", project="proj-b")
        assert r[0]["summary"] == "Web design"
        r2 = store.recall_phases("CLI tool")
        assert len(r2) >= 1

    def test_dedup_upsert(self, store):
        store.write(MemoryEntry(id="same", project="test", phase="Design",
            tags=["v1"], summary="version 1",
            timestamp=time.time()))
        store.write(MemoryEntry(id="same", project="test", phase="Design",
            tags=["v2"], summary="version 2",
            timestamp=time.time()))
        assert store._col_phases.count() == 1
        r = store.recall_phases("version", project="test")
        assert r[0]["summary"] == "version 2"

    def test_empty_recall(self, store):
        assert store.recall_phases("anything") == []
        assert store.recall_functions("anything") == []


# ── Phase extraction ──────────────────────────────

class TestPhaseExtraction:
    def test_design(self, blackboard):
        e = extract_phase_entry("test", "Design", blackboard)
        assert e is not None
        assert any("CLI" in t for t in e.tags)
        assert "counter" in e.tags

    def test_verification(self, blackboard):
        e = extract_phase_entry("test", "Verification", blackboard)
        assert e is not None
        assert "counter.py" in e.tags
        assert "HIGH" in e.tags

    def test_function_entries(self, blackboard):
        entries = extract_function_entries("test", blackboard)
        assert len(entries) == 2
        # 未设置 review_valid（= 没有有效审查记录）→ 三态收紧：不标 verified
        counter = [e for e in entries if e.summary.startswith("count_stats")][0]
        assert "verified" not in counter.tags
        assert "unreviewed" in counter.tags

    def test_function_entries_verified_when_review_clean(self, blackboard):
        """有效审查（review_valid>0）且 issues 为空 → verified。"""
        bb = blackboard
        bb["review_valid"] = 1
        bb["review_SecurityReviewer"] = []     # 查过，无问题
        entries = extract_function_entries("test", bb)
        assert all("verified" in e.tags for e in entries)

    def test_function_entries_all_discarded_unreviewed(self, blackboard):
        """审查全部被丢弃（无有效输出）→ unreviewed，不标 verified
        （"没查过 = 通过"的漏洞修复）。"""
        bb = blackboard
        bb._data.pop("review_SecurityReviewer", None)
        bb["review_discarded"] = 4
        entries = extract_function_entries("test", bb)
        assert all("unreviewed" in e.tags for e in entries)
        assert all("verified" not in e.tags for e in entries)

    def test_function_id_stable(self, blackboard):
        e1 = extract_function_entries("test", blackboard)[0]
        e2 = extract_function_entries("test", blackboard)[0]
        assert e1.id == e2.id

    def test_function_detail_has_code(self, blackboard):
        entries = extract_function_entries("test", blackboard)
        reader = [e for e in entries if "read_lines" in e.summary][0]
        assert "def read_lines" in reader.detail


# ── Shared memory ─────────────────────────────────

class TestSharedMemory:
    def test_shared_chromadb(self, tmp_path):
        import uuid
        chroma = tmp_path / "shared-chroma" / uuid.uuid4().hex[:8]
        s1 = MemoryStore(chroma_dir=str(chroma))
        s1.write(MemoryEntry(id="a-Design", project="proj-a", phase="Design",
            tags=["CLI"], summary="CLI project",
            timestamp=time.time()))

        s2 = MemoryStore(chroma_dir=str(chroma))
        assert s2._col_phases.count() == 1
        r = s2.recall_phases("CLI")
        assert len(r) == 1
        assert r[0]["project"] == "proj-a"


# ── 修复回归 ─────────────────────────────────────

def test_has_issues_matches_by_basename():
    """review issue 带目录前缀（src/counter.py）时仍能标记 has-issues，
    不再因精确匹配漏判而误标 verified。"""
    bb = Blackboard()
    bb["review_valid"] = 1
    bb["modules"] = [{
        "name": "counter", "exports": [
            {"name": "count_stats", "signature": "(lines) -> dict",
             "description": "count stats"},
        ], "files": ["counter.py"], "depends_on": [],
    }]
    bb.codes = {"counter.py": "def count_stats(lines): ..."}
    bb["review_SecurityReviewer"] = [
        {"file": "src/counter.py", "severity": "HIGH", "line": 3,
         "description": "count_stats splits wrongly"}
    ]
    entries = extract_function_entries("p", bb)
    assert entries[0].tags == ["counter", "count_stats", "has-issues"]
    assert "verified" not in entries[0].tags


def test_unmentioned_function_is_unreviewed_not_verified():
    """有效审查存在但某函数未被问题提及 → unreviewed（三态收紧：
    没被查过不等于通过）。"""
    bb = Blackboard()
    bb["review_valid"] = 1
    bb["modules"] = [{
        "name": "ok_mod", "exports": [
            {"name": "ok_fn", "signature": "() -> int", "description": "ok"},
        ], "files": ["ok.py"], "depends_on": [],
    }]
    bb.codes = {"ok.py": "def ok_fn(): return 1"}
    bb["review_SecurityReviewer"] = [
        {"file": "other.py", "severity": "MEDIUM", "line": 1, "description": "x"}
    ]
    entries = extract_function_entries("p", bb)
    assert "unreviewed" in entries[0].tags
    assert "verified" not in entries[0].tags


def test_clean_review_marks_verified():
    """有效审查且最终 issues 为空 → verified（修复后复查通过）。"""
    bb = Blackboard()
    bb["review_valid"] = 1
    bb["modules"] = [{
        "name": "ok_mod", "exports": [
            {"name": "ok_fn", "signature": "() -> int", "description": "ok"},
        ], "files": ["ok.py"], "depends_on": [],
    }]
    bb.codes = {"ok.py": "def ok_fn(): return 1"}
    bb["review_SecurityReviewer"] = []
    entries = extract_function_entries("p", bb)
    assert "verified" in entries[0].tags


def test_query_keywords_extracts_chinese():
    """中文查询块必须被提取（此前只认英文 camelCase/snake_case，
    中文项目召回 exact_match 永远为 False）。"""
    kws = _extract_query_keywords("设计一个命令行字数统计工具")
    assert any("一" <= ch <= "鿿" for kw in kws for ch in kw)
    assert kws[0] == "设计一个命令行字数统计工具"      # 整块作关键词


def test_kw_hit_chinese_shared_bigram():
    """无分词器：查询块与 tag 有公共 2 字子串即命中（'统计' 交集）。"""
    from memory.infrastructure.chroma_store import _kw_hit
    assert _kw_hit(["命令行"], ["设计一个命令行字数统计工具"], "统计行数") is True
    assert _kw_hit([], ["设计一个命令行字数统计工具"], "命令行参数") is True
    assert _kw_hit([], ["设计一个命令行字数统计工具"], "视频剪辑") is False


def test_query_keywords_mixed_english_chinese():
    kws = _extract_query_keywords("实现 count_stats 函数支持多文件")
    assert "count_stats" in kws
    assert any(any("一" <= ch <= "鿿" for ch in kw) for kw in kws)


# ── A1 内容指纹去重 ─────────────────────────────

def test_dedup_skips_same_content_different_project(store, tmp_path):
    """跨任务同内容 → 跳过写入（只留第一条）。"""
    from memory.domain.models import MemoryEntry
    e1 = MemoryEntry(id="proj-a-Design", project="proj-a", phase="Design",
                     tags=["CLI"], summary="CLI 记账本", detail='{"modules": []}')
    store.write(e1)
    assert store._col_phases.count() == 1
    # 同内容、不同任务 → 去重跳过
    e2 = MemoryEntry(id="proj-b-Design", project="proj-b", phase="Design",
                     tags=["CLI"], summary="CLI 记账本", detail='{"modules": []}')
    store.write(e2)
    assert store._col_phases.count() == 1
    # 内容不同 → 正常写入
    e3 = MemoryEntry(id="proj-b-Design", project="proj-b", phase="Design",
                     tags=["CLI"], summary="CLI 记账本", detail='{"modules": ["a"]}')
    store.write(e3)
    assert store._col_phases.count() == 2


def test_dedup_allows_same_key_rerun_refresh(store):
    """同任务重跑（同 key）→ 指纹命中自己不跳过，正常覆盖刷新。"""
    from memory.domain.models import MemoryEntry
    e1 = MemoryEntry(id="p-Design", project="p", phase="Design",
                     tags=["CLI"], summary="CLI", detail="v1")
    store.write(e1)
    e2 = MemoryEntry(id="p-Design", project="p", phase="Design",
                     tags=["CLI"], summary="CLI", detail="v1")
    store.write(e2)
    assert store._col_phases.count() == 1
    r = store._col_phases.get(ids=["p-Design"])
    assert r["metadatas"][0]["fingerprint"]


# ── C2 Coding 记忆内容化 ────────────────────────

def test_coding_entry_detail_has_code(blackboard):
    """C2: Coding 阶段记忆 detail 存代码内容（此前只有文件名）。"""
    e = extract_phase_entry("test", "Coding", blackboard)
    assert e is not None
    assert "def count_stats" in e.detail       # 代码内容进 detail
    assert "counter.py" in e.summary           # 文件名仍保留在 summary


# ── D3 format 不输出空标题段 ─────────────────────

def test_format_function_no_header_only():
    from memory.interfaces.prompt_formatter import format_function_memories
    entries = [{"summary": "f() -> int", "tags": ["m", "f", "verified"],
                "detail": "x" * 1000}]
    # 上限太小 → 一个代码块都放不下 → 返回空（不能只剩标题）
    assert format_function_memories(entries, max_chars=50) == ""
    # 上限足够 → 正常输出
    out = format_function_memories(entries, max_chars=2000)
    assert "PROVEN IMPLEMENTATIONS" in out
    assert "f() -> int" in out


# ── E1 清空 ─────────────────────────────────────

def test_clear_removes_all(store):
    from memory.domain.models import MemoryEntry
    store.write(MemoryEntry(id="a-Design", project="a", phase="Design",
                            tags=[], summary="x"))
    store.write(MemoryEntry(id="b-fn-m-f", project="b", phase="Function",
                            tags=[], summary="y"))
    assert store.clear() == 2
    assert store._col_phases.count() == 0
    assert store._col_functions.count() == 0


# ── M2 未完成项目排除 ─────────────────────────────

def test_recall_excludes_incomplete_projects(store):
    """未完成项目（无 QualityGate 记忆）的早期记忆不进检索池。"""
    import time as _t
    old = _t.time() - 7200     # 2 小时前 = 不豁免
    store.write(MemoryEntry(id="done-Req", project="done-proj",
                            phase="RequirementsDiscussion",
                            tags=["CLI"], summary="done 需求", timestamp=old))
    store.write(MemoryEntry(id="done-QG", project="done-proj",
                            phase="QualityGate", tags=["PASS"],
                            summary="PASS", timestamp=old))
    store.write(MemoryEntry(id="dead-Req", project="dead-proj",
                            phase="RequirementsDiscussion",
                            tags=["CLI"], summary="dead 需求", timestamp=old))
    r = store.recall_phases("CLI", n=5)
    projects = [e["project"] for e in r]
    assert "done-proj" in projects
    assert "dead-proj" not in projects     # 从未交付 → 排除


def test_recall_exempts_fresh_entries(store):
    """1 小时内的新写入豁免（当前运行自己的早期记忆不被排除）。"""
    store.write(MemoryEntry(id="fresh-Req", project="fresh-proj",
                            phase="RequirementsDiscussion",
                            tags=["CLI"], summary="新鲜需求", timestamp=time.time()))
    r = store.recall_phases("CLI", n=5)
    assert any(e["project"] == "fresh-proj" for e in r)


# ── M3 注入去重 ───────────────────────────────────

def test_format_memories_dedupes_projects():
    """同 project 只留最高分一条（碎片化不压制多样性）。"""
    entries = [
        {"project": "a", "phase": "Design", "summary": "a-1", "score": 3.0},
        {"project": "a", "phase": "Coding", "summary": "a-2", "score": 2.0},
        {"project": "b", "phase": "Design", "summary": "b-1", "score": 2.5},
    ]
    out = format_memories(entries, max_chars=500)
    assert "a-1" in out and "a-2" not in out   # 同 project 只留第一条
    assert "b-1" in out


def test_format_functions_dedupes_projects():
    entries = [
        {"project": "a", "summary": "f1", "tags": ["verified"], "detail": "x"},
        {"project": "a", "summary": "f2", "tags": ["verified"], "detail": "y"},
        {"project": "b", "summary": "f3", "tags": ["verified"], "detail": "z"},
    ]
    out = format_function_memories(entries, max_chars=2000)
    assert "f1" in out and "f2" not in out and "f3" in out


# ── M4 容量淘汰 ───────────────────────────────────

def test_eviction_when_over_limit(store, monkeypatch):
    """超上限时按时间戳淘汰最旧。"""
    monkeypatch.setattr("core.config.load_pipeline_config",
                        lambda: {"memory": {"max_phases": 3,
                                            "max_functions": 2000}})
    for i in range(5):
        store.write(MemoryEntry(id=f"p{i}-D", project=f"p{i}", phase="Design",
                                tags=["CLI"], summary=f"s{i}",
                                timestamp=1000 + i))
    assert store._col_phases.count() == 3          # 淘汰 2 条最旧
    ids = store._col_phases.get(limit=10)["ids"]
    assert "p0-D" not in ids and "p1-D" not in ids  # 最旧的被删
    assert "p4-D" in ids
