"""File operation tools."""
import os

from codegen.infrastructure.tools.registry import register, runtime

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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Successfully wrote {len(content)} bytes to '{filename}'."

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
