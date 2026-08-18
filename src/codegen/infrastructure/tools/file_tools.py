"""File operation tools."""
import os

from codegen.infrastructure.tools.registry import register, runtime

# read_file 单文件大小上限：超大文件（误拷入的第三方库/checkpoint JSON）
# 直接拒绝，防止一次读取撑爆 LLM 上下文
_MAX_READ_CHARS = 200_000
# write_file 单文件写入上限：整包拷贝第三方库源文件动辄数 MB，直接拒绝
_MAX_WRITE_CHARS = 500_000
# 内部目录（与 list_files 一致）：pipeline 状态文件不出现在 agent 视野
_READ_SKIP = {".venv", "__pycache__", ".git", ".task_outputs", ".devforge"}

# 诊断脚本名黑名单：agent 反复写 _dump.py/_probe.py 等探查脚本（prompt
# 禁止无效）—— 平台层硬拦截，探查请用 read_many/list_files
_SCRATCH_PREFIXES = ("_dump", "_probe", "_find", "_check", "_search",
                     "_cat", "_tail", "_read", "_verify", "_bootstrap",
                     "_reinstall", "_which", "_run_", "_force", "_venv",
                     "_install", "_smoke")
_SCRATCH_NAMES = {"probe.py", "diag.py", "sanity.py", "tiny.py",
                  "check.py", "show.py", "runner.py", "verify.py"}

def _is_scratch_name(filename: str) -> bool:
    base = os.path.basename(filename).lower()
    return (base in _SCRATCH_NAMES
            or any(base.startswith(p) for p in _SCRATCH_PREFIXES))

def _safe_path(filename: str) -> str:
    """Resolve *filename* against the project root, rejecting path traversal."""
    root = os.path.realpath(runtime().project_dir)
    resolved = os.path.realpath(os.path.join(root, filename))
    common = os.path.commonpath([root, resolved])
    if os.path.realpath(common) != root:
        raise ValueError(f"Path escapes workspace: {filename}")
    return resolved

@register(
    name="read_file",
    description="Read a file's contents from the current project. "
                "Files already read this session return a short 'already "
                "read' notice instead of the content again — read files "
                "ONCE. For multiple files, use read_many in one call.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File path relative to project root, e.g., 'main.py' or 'src/utils.py'"
            }
        },
        "additionalProperties": False,
        "required": ["filename"]
    }
)
def read_file(filename: str) -> str:
    rt = runtime()
    try:
        path = _safe_path(filename)
    except ValueError as e:
        return f"Error: {e}"
    # 已读拦截：本 agent 本轮已读过该文件（read_file 或 read_many）→
    # 不再重发内容（内容在对话历史里）。模型反复读同一文件是工具循环
    # 里最贵的重复（12k 内容 × N 轮）；write_file 后记录失效可重读。
    if rt.is_read(path):
        return (f"(already read this session — the full content is in your "
                "conversation history. Do NOT re-read it. If the earlier "
                "result was truncated by compaction, use read_many once.)")
    if not os.path.exists(path):
        return f"Error: file '{filename}' does not exist in the project."
    rel = os.path.relpath(path, os.path.realpath(runtime().project_dir))
    if set(rel.split(os.sep)) & _READ_SKIP:
        return (f"Error: '{filename}' is inside an internal directory "
                f"({_READ_SKIP & set(rel.split(os.sep))}) — not readable.")
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return f"Error reading '{filename}': {e}"
    if size > _MAX_READ_CHARS:
        return (f"Error: '{filename}' is {size} bytes — too large to read in full "
                f"(limit {_MAX_READ_CHARS}). List the file with list_files or "
                "skip it if it is not your own source code.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        rt.mark_read(path)
        return content if content else "(empty file)"
    except Exception as e:
        return f"Error reading '{filename}': {e}"

@register(
    name="write_file",
    description="Create or overwrite a file in the current project.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File path relative to project root"
            },
            "content": {
                "type": "string",
                "description": "Complete file content to write"
            }
        },
        "required": ["filename", "content"]
    }
)
def write_file(filename: str, content: str) -> str:
    try:
        path = _safe_path(filename)
    except ValueError as e:
        return f"Error: {e}"
    if _is_scratch_name(filename):
        return (f"Error: '{filename}' looks like a diagnostic scratch file — "
                "do NOT create it. Inspect the project with read_many/"
                "list_files instead, and deliver only real source files.")
    if len(content) > _MAX_WRITE_CHARS:
        return (f"Error: '{filename}' would be {len(content)} chars — over the "
                f"{_MAX_WRITE_CHARS} limit. You must write only your own source "
                "code, never copy in third-party library files.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    # 内容已变 → 已读记录失效（允许重新读取新内容）
    runtime().invalidate_file(path)
    return f"Successfully wrote {len(content)} bytes to '{filename}'."

# read_many 批量读取的上限：一次调用最多读的文件数 / 合并结果字符数
_MAX_BATCH_FILES = 12
_MAX_BATCH_CHARS = 12_000

@register(
    name="read_many",
    description="Read MULTIPLE files in ONE call (batch). Pass the file "
                "paths you need as a list — use this instead of repeated "
                "read_file calls to save round-trips. Each file is returned "
                "with its name as a header; output is truncated if huge. "
                "Files already read this session are reported as already-read "
                "instead of re-returning content.",
    parameters={
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "File paths relative to project root, e.g. "
                               "['src/cli.py', 'src/scanner.py']",
            }
        },
        "additionalProperties": False,
        "required": ["files"]
    }
)
def read_many(files: list) -> str:
    if not isinstance(files, list) or not files:
        return "Error: 'files' must be a non-empty list of relative paths."
    if len(files) > _MAX_BATCH_FILES:
        return (f"Error: at most {_MAX_BATCH_FILES} files per read_many call "
                f"(got {len(files)}). Split into smaller batches.")
    parts: list[str] = []
    total = 0
    rt = runtime()
    for filename in files:
        try:
            path = _safe_path(filename)
        except ValueError as e:
            parts.append(f"===== {filename} =====\nError: {e}")
            continue
        # 已读拦截（与 read_file 同一集合）：批量读里夹带已读文件
        # 是模型常见重复，直接给提示不重发内容
        if rt.is_read(path):
            parts.append(f"===== {filename} =====\n(already read this session — "
                         "see earlier result in the conversation)")
            continue
        rel = os.path.relpath(path, os.path.realpath(runtime().project_dir))
        if set(rel.split(os.sep)) & _READ_SKIP:
            parts.append(f"===== {filename} =====\nError: internal directory — not readable.")
            continue
        if not os.path.exists(path):
            parts.append(f"===== {filename} =====\n(missing — not found)")
            continue
        try:
            size = os.path.getsize(path)
        except OSError as e:
            parts.append(f"===== {filename} =====\nError: {e}")
            continue
        if size > _MAX_READ_CHARS:
            parts.append(f"===== {filename} =====\nError: too large "
                         f"({size} bytes > {_MAX_READ_CHARS}) — read_file it in parts.")
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            parts.append(f"===== {filename} =====\nError reading: {e}")
            continue
        rt.mark_read(path)
        parts.append(f"===== {filename} =====\n{content}")
        total += len(content)
    result = "\n".join(parts)
    if total > _MAX_BATCH_CHARS:
        result = result[:_MAX_BATCH_CHARS] + \
            f"\n…(batch output truncated {total} → {_MAX_BATCH_CHARS} chars)"
    return result

@register(
    name="delete_file",
    description="Delete a file in the current project (relative path, "
                "project root only). Use to remove scratch/diagnostic files.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File path relative to project root, e.g., 'scratch.py'"
            }
        },
        "additionalProperties": False,
        "required": ["filename"]
    }
)
def delete_file(filename: str) -> str:
    try:
        path = _safe_path(filename)
    except ValueError as e:
        return f"Error: {e}"
    if not os.path.exists(path):
        return f"Error: file '{filename}' does not exist."
    if os.path.isdir(path):
        return f"Error: '{filename}' is a directory — only files can be deleted."
    try:
        os.remove(path)
    except OSError as e:
        return f"Error deleting '{filename}': {e}"
    return f"Deleted '{filename}'."

@register(
    name="list_files",
    description="List all files in the current project directory.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Optional glob pattern, e.g., '*.py'. Default: all files.",
                "default": "*"
            }
        }
    }
)
def list_files(pattern: str = "*") -> str:
    import glob as glob_mod
    root = runtime().project_dir
    # Reject traversal / absolute patterns BEFORE joining into the glob —
    # '../secrets' or 'C:\\x' would otherwise escape the workspace .
    if not pattern or ".." in pattern or os.path.isabs(pattern) \
            or pattern.startswith(("/", "\\")) or ":" in pattern[:2]:
        return "Error: pattern must be a relative glob (no '..', no absolute paths)"
    files = glob_mod.glob(os.path.join(root, "**", pattern), recursive=True)
    _SKIP = {'.venv', '__pycache__', '.git', '.task_outputs', '.devforge'}
    relative = []
    for f in files:
        parts = set(os.path.relpath(f, root).split(os.sep))
        if not (parts & _SKIP):
            relative.append(os.path.relpath(f, root))
        if len(relative) >= 100:
            break
    return "\n".join(relative) if relative else "(no files found)"
