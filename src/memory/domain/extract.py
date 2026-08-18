"""Extract memory entries from a Blackboard after each phase."""
import json
import logging
import os
import re
import time

from memory.domain.models import MemoryEntry

_log = logging.getLogger(__name__)

# 错误签名提取（M1 learn_fix_pattern）：从测试输出/错误文本中用规则提取，
# 不调 LLM —— 确定性、零成本。按优先级匹配，取第一条命中。
# 元组 = (正则, 类型标签)；"__TYPE__" = 类型从捕获组 1 取（异常类名）。
_FIX_SIG_PATTERNS = (
    (r"ModuleNotFoundError:\s*No module named ['\"]([^'\"]+)['\"]",
     "ModuleNotFoundError"),
    (r"ImportError:\s*cannot import name ['\"]([^'\"]+)['\"]",
     "ImportError"),
    (r"NameError:\s*name ['\"]([^'\"]+)['\"] is not defined",
     "NameError"),
    (r"AttributeError:\s*['\"]?([^'\"]{1,40})['\"]?",
     "AttributeError"),
    (r"(TypeError|ValueError|KeyError|IndexError|AssertionError):\s*(.{1,80})",
     "__TYPE__"),
    (r"FAILED\s+(\S+)",
     "TestFailure"),
)

def _extract_error_signature(output: str) -> str:
    """从输出提取结构化错误签名（如 "ModuleNotFoundError: yaml"、
    "AssertionError: expected 2"）；无命中返回 ""。"""
    for pat, kind in _FIX_SIG_PATTERNS:
        m = re.search(pat, output or "")
        if not m:
            continue
        if kind == "__TYPE__":
            return f"{m.group(1)}: {(m.group(2) or '').strip()[:80]}"
        name = (m.group(1) or "").strip()[:80]
        return f"{kind}: {name}" if name else kind
    return ""

def extract_fix_pattern(
    project: str, before_codes: dict, after_codes: dict,
    test_output: str,
) -> MemoryEntry | None:
    """修复轮后提取修复模式（M1）：只提取"验证过的修复"（调用方保证
    修复后测试通过）。返回 None = 无变化/无错误签名。

    detail 存修复前后对照（before[:300] + after[:800]），tags 存错误类型
    与变化文件名 —— 召回按错误签名 + 文件名关键词。
    """
    changed = [f for f in after_codes
               if before_codes.get(f) != after_codes.get(f)]
    if not changed:
        return None
    sig = _extract_error_signature(test_output)
    if not sig:
        return None
    fname = changed[0]
    before = (before_codes.get(fname) or "")[:300]
    after = (after_codes.get(fname) or "")[:800]
    tags = [sig.split(":", 1)[0], fname] \
        + [f.split("/")[-1] for f in changed[:3]]
    return MemoryEntry(
        id=f"{project}-fix-{re.sub(r'[^\\w]', '_', sig)[:40]}",
        project=project,
        phase="FixPattern",
        tags=_clean_tags(tags),
        summary=f"{sig} — 修复 {len(changed)} 个文件",
        detail=f"### 修复前 ({fname})\n{before}\n\n"
               f"### 修复后 ({fname})\n{after}",
        timestamp=time.time(),
    )

def _clean_tags(tags: list) -> list[str]:
    """Drop None / non-str / blank tags — '' and '  ' would pollute recall."""
    return [t for t in tags if isinstance(t, str) and t.strip()]

# review_ 前缀下的元数据键（非审查者输出）—— 提取 issues 时排除
_REVIEW_META_KEYS = frozenset({"review_valid", "review_discarded"})

def extract_phase_entry(project: str, phase: str, blackboard) -> MemoryEntry | None:
    """Build a Phase-level memory entry from the blackboard after *phase*."""
    ts = time.time()
    pid = f"{project}-{phase}"
    tags = [project, phase]

    try:
        if phase == "RequirementsDiscussion":
            req = blackboard.get("requirements", {})
            tags += req.get("core_features", [])[:5]
            tags.append(req.get("product_type", ""))
            tags.append(req.get("language", ""))
            return MemoryEntry(
                id=pid, project=project, phase=phase, tags=_clean_tags(tags),
                summary=f"{req.get('product_type','?')} '{project}', 功能: {', '.join(req.get('core_features', [])[:3])}",
                detail=json.dumps(req, ensure_ascii=False),
                timestamp=ts,
            )

        if phase == "Design":
            modules = blackboard.get("modules", [])
            tags.append(blackboard.get("modality", ""))
            tags.append(blackboard.get("language", ""))
            tags += [m["name"] for m in modules]
            return MemoryEntry(
                id=pid, project=project, phase=phase, tags=_clean_tags(tags),
                summary=f"{blackboard.get('modality','?')} '{project}', {blackboard.get('language','?')}, {len(modules)} 模块: {', '.join(m['name'] for m in modules[:5])}",
                detail=json.dumps(modules, ensure_ascii=False),
                timestamp=ts,
            )

        if phase == "Coding":
            codes = getattr(blackboard, "codes", {}) if hasattr(blackboard, "codes") else {}
            tags += list(codes.keys())[:10]
            tags.append(blackboard.get("language", ""))
            # detail 存代码内容摘要（此前只有文件名清单，检索价值低；
            # 函数级已有完整代码，这里给"这个任务实现过什么"的检索入口）
            code_digest = "\n\n".join(
                f"### {name}\n{src[:300]}" for name, src in list(codes.items())[:6])
            return MemoryEntry(
                id=pid, project=project, phase=phase, tags=_clean_tags(tags),
                summary=f"{len(codes)} 文件: {', '.join(list(codes.keys())[:5])}",
                detail=code_digest[:2000],
                timestamp=ts,
            )

        if phase == "Verification":
            issues_all = []
            # 防御式访问：_data 是私有属性，且 review_ 键的值可能是非
            # list（历史 checkpoint 的字符串等）—— 直接遍历会崩掉整个
            # 阶段记忆提取
            bb_data = getattr(blackboard, "_data", {}) or {}
            for key, value in bb_data.items():
                if key.startswith("review_") and key not in _REVIEW_META_KEYS \
                        and isinstance(value, list):
                    for issue in value:
                        tags += [issue.get("file", ""), issue.get("severity", "")]
                        issues_all.append(issue)
            return MemoryEntry(
                id=pid, project=project, phase=phase, tags=_clean_tags(tags),
                summary=f"{len(issues_all)} 个问题: " + ", ".join(
                    f"{i.get('severity','?')}: {i.get('file','?')}" for i in issues_all[:3]),
                detail=json.dumps(issues_all, ensure_ascii=False),
                timestamp=ts,
            )

        if phase == "QualityGate":
            report = blackboard.get("quality_gate", {})
            verdict = report.get("verdict", "?")
            score = report.get("score", 0)
            tags.append(verdict)
            return MemoryEntry(
                id=pid, project=project, phase=phase, tags=_clean_tags(tags),
                summary=f"{verdict}, {score}/100",
                detail=json.dumps(report, ensure_ascii=False),
                timestamp=ts,
            )
    except Exception:
        _log.exception("Failed to extract memory for %s/%s", project, phase)

    return None

def extract_function_entries(project: str, blackboard) -> list[MemoryEntry]:
    """Build Function-level entries from module exports + code + review issues.

    三态标记（B 收紧）：
    - ``verified``     — 最终轮有有效审查输出且 issues 为空（查过且没问题）
    - ``has-issues``   — 最终轮 issues 命中该函数名
    - ``unreviewed``   — 最终轮审查全部被丢弃 / 无有效审查 / 未被问题提及
                          （"没查过"不等于"通过"）

    A function is tagged ``has-issues`` only when its own name appears in
    a review issue — not when a sibling function in the same file is broken.
    """
    entries: list[MemoryEntry] = []
    modules = blackboard.get("modules", [])
    codes = getattr(blackboard, "codes", {}) if hasattr(blackboard, "codes") else {}

    issues_by_file: dict[str, list] = {}
    bb_data = getattr(blackboard, "_data", {}) or {}
    for key, value in bb_data.items():
        # review_ 前缀含元数据键（review_valid/review_discarded），要排除
        if key.startswith("review_") and key not in _REVIEW_META_KEYS \
                and isinstance(value, list):
            for issue in value:
                fname = issue.get("file", "")
                issues_by_file.setdefault(fname, []).append(issue)

    ts = time.time()
    # issue 的 file 字段常带目录前缀（"src/counter.py"），模块 files 是裸名
    # （"counter.py"）——精确匹配会漏判，误标 verified。统一按 basename 归类。
    issues_by_base: dict[str, list] = {}
    for fname, issues in issues_by_file.items():
        issues_by_base.setdefault(os.path.basename(fname), []).extend(issues)

    # 最终轮审查质量：review_valid = 有合法输出的审查者数
    valid = int(blackboard.get("review_valid", 0) or 0)
    if valid > 0:
        any_issue = any(issues_by_base.values())
        if any_issue:
            issue_blob = " ".join(
                str(i.get("description", "")) + " " + str(i.get("file", ""))
                for issues in issues_by_base.values() for i in issues)

            def _state(fn_name: str) -> str:
                pattern = re.compile(rf"\b{re.escape(fn_name)}\b")
                if pattern.search(issue_blob):
                    return "has-issues"
                return "unreviewed"
        else:
            # 查过且问题为空 → 全部 verified（修复后复查通过）
            def _state(fn_name: str) -> str:
                return "verified"
    else:
        # 最终轮没有有效审查（全部被丢弃/未运行）→ 不标 verified
        def _state(fn_name: str) -> str:
            return "unreviewed"

    for mod in modules:
        for exp in mod.get("exports", []):
            fn_name = exp.get("name", "")
            fn_sig = exp.get("signature", "")
            fn_desc = exp.get("description", "")
            mod_files = mod.get("files", [])
            code_snippet = ""
            for fname, src in codes.items():
                if any(re.search(rf"\b{re.escape(mf)}\b", fname)
                       for mf in mod_files):
                    code_snippet += src + "\n"
            entries.append(MemoryEntry(
                id=f"{project}-fn-{mod['name']}-{fn_name}",
                project=project,
                phase="Function",
                tags=_clean_tags([
                    mod["name"], fn_name, _state(fn_name),
                ]),
                summary=f"{fn_name}{fn_sig} — {fn_desc}",
                detail=code_snippet[:8000] or f"{fn_name}{fn_sig} — {fn_desc}",
                timestamp=ts,
            ))
            ts += 0.001
    return entries
