"""Memory system demo — run this to see write/recall/cross-project in action."""
import time
from pathlib import Path

from memory.infrastructure.chroma_store import (
    MemoryEntry, MemoryStore, format_function_memories, format_memories,
)
from codegen.domain.blackboard import Blackboard

# ═══════════════════════════════════════════════════════
# Setup: shared WareHouse with 2 projects
# ═══════════════════════════════════════════════════════

wh = Path(__file__).resolve().parent.parent / "WareHouse"
wh.mkdir(exist_ok=True)

proj_a = wh / "wc-py_DevForge_demo"; proj_a.mkdir(exist_ok=True)
proj_b = wh / "taskapp_DevForge_demo"; proj_b.mkdir(exist_ok=True)

store_a = MemoryStore(str(proj_a))
store_b = MemoryStore(str(proj_b))

print("=" * 60)
print("  STEP 1 — 模拟项目 A (wc-py) Pipeline 写入记忆")
print("=" * 60)
print()

# Simulate a full pipeline run writing to store_a
entries = [
    MemoryEntry(id="wc-py-Requirements", project="wc-py", phase="RequirementsDiscussion",
        tags=["CLI tool", "Python", "wc"], summary="CLI字数统计, Python, 功能: 行数/单词/字符"),
    MemoryEntry(id="wc-py-Design", project="wc-py", phase="Design",
        tags=["CLI tool", "Python", "cli", "counter", "file_reader"],
        summary="3模块: cli, counter, file_reader — 接口契约: count_stats(lines)->dict, read_file(path)->list"),
    MemoryEntry(id="wc-py-fn-counter-count_stats", project="wc-py", phase="Function",
        tags=["counter", "count_stats", "verified"],
        summary="count_stats(lines: list[str]) -> dict — 统计行数/单词数/字符数",
        detail="def count_stats(lines):\n    import re\n    return {'lines': len(lines), 'words': sum(len(re.findall(r'\\w+',l)) for l in lines)}"),
    MemoryEntry(id="wc-py-fn-file_reader-read_file", project="wc-py", phase="Function",
        tags=["file_reader", "read_file", "verified"],
        summary="read_file(path: str, encoding: str) -> list[str] — 读取文件",
        detail="def read_file(path, encoding='utf-8'):\n    import codecs\n    with codecs.open(path, 'r', encoding) as f: return f.readlines()"),
    MemoryEntry(id="wc-py-fn-cli-main", project="wc-py", phase="Function",
        tags=["cli", "main", "verified"],
        summary="main() -> None — CLI入口",
        detail="def main():\n    import sys, json\n    ..."),
    MemoryEntry(id="wc-py-Verification", project="wc-py", phase="Verification",
        tags=["counter.py", "HIGH", "split", "bug"],
        summary="2个问题: HIGH: counter.py split()按空格分词未识别标点, MEDIUM: cli.py 参数校验"),
]

now = time.time()
for e in entries:
    e.timestamp = now
    store_a.write(e)
print()

print("=" * 60)
print("  STEP 2 — 项目 B (taskapp) 新项目，CTO 设计时跨项目检索")
print("=" * 60)
print()

# CTO queries: natural language → semantic search
print('--- CTO 自然语言查询 ---')
r_cto_nl = store_b.recall_phases("如何设计一个处理文本文件的命令行工具")
for e in r_cto_nl:
    print(f'  [{e["project"]}] [{e["phase"]}] {e["summary"][:60]}')

print()
print('prompt注入 (format_memories):')
print(format_memories(r_cto_nl))
print()

# CTO queries: with keyword → grep boost
print('--- CTO 关键词查询 (含 counter) ---')
r_cto_kw = store_b.recall_phases("counter 模块怎么设计")
for e in r_cto_kw:
    print(f'  {e["score"]:.1f} [{e["project"]}] [{e["phase"]}] {e["summary"][:60]}')
print()

print("=" * 60)
print("  STEP 3 — 项目 B Coder 实现 counter 模块，函数级检索")
print("=" * 60)
print()

# Coder queries: function name → grep precision
print('--- Coder grep查询: count_stats ---')
r_fn = store_b.recall_functions("count_stats counter", project="", n=3)
for e in r_fn:
    print(f'  {e["score"]:.1f} [{e["phase"]}] {e["summary"][:60]}')
    if e["detail"]:
        print(f'  detail preview: {e["detail"][:60]}...')

print()
print('prompt注入 (format_function_memories):')
print(format_function_memories(r_fn))
print()

# Coder queries: natural language → semantic
print('--- Coder 语义查询: 文件读取编码处理 ---')
r_fn_sem = store_b.recall_functions("文件读取编码处理", project="", n=3)
for e in r_fn_sem:
    print(f'  {e["score"]:.1f} [{e["project"]}] [{e["phase"]}] {e["summary"][:60]}')
print()

print("=" * 60)
print("  STEP 4 — 本项目内检索 vs 跨项目")
print("=" * 60)
print()

# Simulate project B writing its own memories
store_b.write(MemoryEntry(id="taskapp-Design", project="taskapp", phase="Design",
    tags=["web app", "React", "auth", "api"], summary="Web任务管理, React+FastAPI, 4模块: auth/api/db/ui",
    timestamp=now + 1))
store_b.write(MemoryEntry(id="taskapp-fn-auth-login", project="taskapp", phase="Function",
    tags=["auth", "login", "verified"],
    summary="login(username, password) -> token — 用户登录",
    detail="def login(username, password):\n    ...",
    timestamp=now + 1))

print('--- 本项目检索 (project="taskapp") ---')
r_same = store_b.recall_phases("login", project="taskapp")
for e in r_same:
    print(f'  [{e["project"]}] {e["summary"][:60]}')
print(f'  跨项目结果数: {len(r_same)} (只看taskapp)')

print()
print('--- 跨项目检索 (project="") ---')
r_cross = store_b.recall_phases("login")
for e in r_cross:
    print(f'  {e["score"]:.1f} [{e["project"]}] {e["summary"][:60]}')
print(f'  跨项目结果数: {len(r_cross)} (所有项目)')

print()
print("=" * 60)
print("  STEP 5 — 确认共享存储 (store_a 能看到store_b的写入)")
print("=" * 60)
a_count = store_a._col_phases.count() + store_a._col_functions.count()
b_count = store_b._col_phases.count() + store_b._col_functions.count()
print(f'  store_a count: {a_count}')
print(f'  store_b count: {b_count}')
shared = a_count == b_count
print(f'  共享确认: {"PASS" if shared else "FAIL"}')
print()

print("=" * 60)
print("  演示完成。现在启动应用，输入'命令行字数统计工具'体验真实流程")
print("=" * 60)
