"""Search & edit tools — locate code with grep, patch files without full rewrite.

``grep_file`` — regex search across project source files (read-only).
``edit_file`` — replace one exact string occurrence in a file (line-level patch,
avoids the token cost and drift risk of full-file rewrite via write_file).
"""
import os
import re

from codegen.infrastructure.tools.file_tools import (
    _MAX_READ_CHARS,
    _MAX_WRITE_CHARS,
    _READ_SKIP,
    _module_write_allowlist,
    _safe_path,
)
from codegen.infrastructure.tools.registry import register, runtime

# grep 结果上限：命中过多时截断并提示（防止一次调用回填爆上下文）
_MAX_GREP_RESULTS = 30
# grep 单文件大小上限：超大文件（误拷第三方库）跳过并提示
_MAX_GREP_FILE_CHARS = 200_000


@register(
    name="grep_file",
    description="Search project files for a regex pattern. Returns "
                "'path:lineno: line-text' matches (case-sensitive by default; "
                "pass ignore_case=true for case-insensitive). Prefer this over "
                "reading whole files when you only need to locate definitions, "
                "call sites, or error strings. Results are truncated at 30 "
                "matches — narrow the pattern or glob if you need more.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression to search for, e.g. "
                               "'def parse_args' or 'TODO'"
            },
            "glob": {
                "type": "string",
                "description": "Filename glob filter, e.g. '*.py', "
                               "'src/*.py'. Default: *.py",
                "default": "*.py"
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive search. Default: false",
                "default": False
            },
        },
        "additionalProperties": False,
        "required": ["pattern"]
    }
)
def grep_file(pattern: str, glob: str = "*.py", ignore_case: bool = False) -> str:
    if not pattern or not isinstance(pattern, str):
        return "Error: 'pattern' must be a non-empty string."
    try:
        flags = re.IGNORECASE if ignore_case else 0
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        return f"Error: invalid regex '{pattern}': {exc}"
    root = os.path.realpath(runtime().project_dir)
    if not os.path.isdir(root):
        return "Error: project directory not found."
    if glob and (".." in glob or os.path.isabs(glob)):
        return "Error: 'glob' must be a relative filename pattern."
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _READ_SKIP]
        for name in filenames:
            if len(matches) >= _MAX_GREP_RESULTS:
                break
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if not _fnmatch(rel, name, glob):
                continue
            path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > _MAX_GREP_FILE_CHARS:
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        if compiled.search(line):
                            text = line.rstrip("\n")[:200]
                            matches.append(f"{rel}:{lineno}: {text}")
                            if len(matches) >= _MAX_GREP_RESULTS:
                                break
            except (OSError, UnicodeError):
                continue
    if not matches:
        return f"(no matches for /{pattern}/ in '{glob}')"
    head = "\n".join(matches)
    if len(matches) >= _MAX_GREP_RESULTS:
        head += "\n…(matches truncated at 30 — narrow the pattern or glob)"
    return head


def _fnmatch(relpath: str, basename: str, glob: str) -> bool:
    """匹配 relpath 或 basename —— '*.py' 应命中任意深度的 .py 文件。"""
    import fnmatch as _fnmatch_mod
    return (_fnmatch_mod.fnmatch(basename, glob)
            or _fnmatch_mod.fnmatch(relpath, glob)
            or _fnmatch_mod.fnmatch(relpath, glob.replace("\\", "/")))


@register(
    name="edit_file",
    description="Replace ONE exact text occurrence in a file — a line-level "
                "patch without rewriting the whole file. Pass enough "
                "surrounding context in 'old_string' to make the match unique "
                "(if the text appears multiple times, the tool errors and you "
                "must include more context). Use for small fixes; use "
                "write_file for new files or wholesale rewrites.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File path relative to project root, e.g. 'main.py'"
            },
            "old_string": {
                "type": "string",
                "description": "Exact text currently in the file to replace — "
                               "must appear exactly once"
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text"
            },
        },
        "additionalProperties": False,
        "required": ["filename", "old_string", "new_string"]
    }
)
def edit_file(filename: str, old_string: str, new_string: str) -> str:
    if not old_string:
        return "Error: 'old_string' must be non-empty."
    try:
        path = _safe_path(filename)
    except ValueError as exc:
        return f"Error: {exc}"
    rel = os.path.relpath(path, os.path.realpath(runtime().project_dir))
    if set(rel.split(os.sep)) & _READ_SKIP:
        return (f"Error: '{filename}' is inside an internal directory "
                f"({_READ_SKIP & set(rel.split(os.sep))}) — not editable.")
    # 模块文件白名单（与 write_file 一致）：并行 coder 不能改别人的文件
    allowed = _module_write_allowlist(runtime().current_agent)
    if allowed is not None and os.path.normpath(rel) not in allowed:
        return (f"Error: '{filename}' is not in your module's file list — "
                "you may only edit your own module files: "
                f"{sorted(allowed)}")
    if not os.path.exists(path):
        return f"Error: file '{filename}' does not exist in the project."
    if os.path.getsize(path) > _MAX_READ_CHARS:
        return (f"Error: '{filename}' is too large to edit safely "
                f"(> {_MAX_READ_CHARS} bytes).")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except (OSError, UnicodeError) as exc:
        return f"Error reading '{filename}': {exc}"
    count = content.count(old_string)
    if count == 0:
        return (f"Error: 'old_string' not found in '{filename}'. The file has "
                "changed since you last read it — read it again and match the "
                "exact current text.")
    if count > 1:
        return (f"Error: 'old_string' appears {count} times in '{filename}' — "
                "include more surrounding context to make the match unique.")
    new_content = content.replace(old_string, new_string, 1)
    if len(new_content) > _MAX_WRITE_CHARS:
        return (f"Error: edited file would be {len(new_content)} chars — over "
                f"the {_MAX_WRITE_CHARS} limit.")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
    except OSError as exc:
        return f"Error writing '{filename}': {exc}"
    # 内容已变 → 已读记录失效 + 工具缓存失效（允许重读/重跑新结果）
    runtime().invalidate_file(path)
    return f"Edited '{filename}' ({count} match replaced)."
