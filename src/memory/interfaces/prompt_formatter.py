"""Format memory recall results for prompt injection."""

def format_memories(entries: list[dict], max_chars: int = 500) -> str:
    """Format Phase-level recall for CTO prompt injection.

    M3: 同 project 只留最高分一条 —— 同语义任务碎片化（如"字数统计"
    分属 6 个项目键）会占满 top-n，压制检索多样性。
    """
    if not entries:
        return ""
    seen_projects: set[str] = set()
    lines = []
    used = 0
    for e in entries:
        proj = str(e.get("project", ""))
        if proj and proj in seen_projects:
            continue
        seen_projects.add(proj)
        line = f"- [{e['phase']}] {e['summary']}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return "--- RELEVANT PAST EXPERIENCE ---\n" + "\n".join(lines)

def format_function_memories(entries: list[dict], max_chars: int = 1200) -> str:
    """Format Function-level recall for Coder prompt injection.

    D3: 先算总长再输出 —— 上限过小时不能只剩一个空标题段。
    """
    if not entries:
        return ""
    verified = [e for e in entries if "verified" in e.get("tags", [])]
    if not verified:
        return ""
    # 同 project 只留一条（同语义任务碎片化会占满注入预算）
    seen_projects: set[str] = set()
    deduped = []
    for e in verified:
        proj = str(e.get("project", ""))
        if proj and proj in seen_projects:
            continue
        seen_projects.add(proj)
        deduped.append(e)
    header = "--- PROVEN IMPLEMENTATIONS (from past projects) ---"
    blocks: list[str] = []
    used = len(header)
    for e in deduped:
        block = f"### {e['summary']}\n```\n{e['detail'][:800]}\n```"
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    if not blocks:
        return ""
    return header + "\n" + "\n".join(blocks)
