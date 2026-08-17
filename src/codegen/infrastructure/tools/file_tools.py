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
    description="Read a file's contents from the current project. Returns the file content or an error message.",
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
    try:
        path = _safe_path(filename)
    except ValueError as e:
        return f"Error: {e}"
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
    if len(content) > _MAX_WRITE_CHARS:
        return (f"Error: '{filename}' would be {len(content)} chars — over the "
                f"{_MAX_WRITE_CHARS} limit. You must write only your own source "
                "code, never copy in third-party library files.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Successfully wrote {len(content)} bytes to '{filename}'."

# read_many 批量读取的上限：一次调用最多读的文件数 / 合并结果字符数
_MAX_BATCH_FILES = 12
_MAX_BATCH_CHARS = 12_000

@register(
    name="read_many",
    description="Read MULTIPLE files in ONE call (batch). Pass the file "
                "paths you need as a list — use this instead of repeated "
                "read_file calls to save round-trips. Each file is returned "
                "with its name as a header; output is truncated if huge.",
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
    for filename in files:
        try:
            path = _safe_path(filename)
        except ValueError as e:
            parts.append(f"===== {filename} =====\nError: {e}")
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
