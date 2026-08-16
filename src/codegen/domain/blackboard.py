"""Shared Blackboard — global workspace for multi-agent collaboration.

All agents can read/write structured knowledge here, replacing ad-hoc
the old env_dict with typed, queryable storage.
"""
import json
import time
from typing import Optional

from codegen.domain.contracts import Contract

__all__ = ["Blackboard", "Contract"]   # Contract 兼容 re-export（收紧）

class Blackboard:
    """Shared workspace — the single source of truth for all pipeline data.

    Key-value store + code files + documents + module contracts.
    """

    def __init__(self):
        self._data: dict = {
            "directory": "",
            "task_prompt": "",
            "task_description": "",
            "modality": "",
            "language": "",
        }

        # Code and documents
        self.codes: dict[str, str] = {}
        self.docs: dict[str, str] = {}

        self.contracts: dict[str, Contract] = {}
        self.module_graph: dict = {}

    # ----- Dict-like access -----

    def __getitem__(self, key):          return self._data[key]
    def __setitem__(self, key, value):   self._data[key] = value
    def __contains__(self, key):         return key in self._data
    def get(self, key, default=None):    return self._data.get(key, default)
    def setdefault(self, key, default):  return self._data.setdefault(key, default)
    def update(self, d: dict):           self._data.update(d)

    # ----- Contract Management -----

    def publish_contract(self, module: str, exports: list, dependencies: list,
                         author: str = "") -> Contract:
        """Publish or update a module's API contract."""
        version = 1
        if module in self.contracts:
            version = self.contracts[module].version + 1
        contract = Contract(
            module=module,
            version=version,
            exports=exports,
            dependencies=dependencies,
            updated_at=time.time(),
            updated_by=author,
        )
        self.contracts[module] = contract
        return contract

    def get_contract(self, module: str) -> Optional[Contract]:
        return self.contracts.get(module)

    # ----- Module Graph -----

    def set_module_graph(self, graph: dict):
        """Set module dependency graph. {module: [depends_on]}"""
        self.module_graph = graph

    def get_downstream_modules(self, module: str) -> list[str]:
        """Get modules that depend on this one."""
        return [m for m, deps in self.module_graph.items() if module in deps]

    # ── Code & Document management ─────────────────

    def file_list(self) -> str:
        """Return just filenames (no code) — for agents that use read_file."""
        return "\n".join(f"- {name}" for name in sorted(self.codes.keys())) or "(no files)"

    def get_codes(self) -> str:
        """Render all code files for prompt context."""
        parts = []
        for name, src in self.codes.items():
            parts.append(f"{name}\n```\n{src}\n```\n")
        return "\n".join(parts)

    def reload_codes(self, directory: str):
        """Re-scan .py files from disk into ``self.codes``."""
        import logging
        import os
        _log = logging.getLogger(__name__)
        _SKIP = {'.venv', '__pycache__', '.git', '.task_outputs', '.devforge'}
        self.codes.clear()
        for root, _dirs, files in os.walk(directory):
            _dirs[:] = [d for d in _dirs if d not in _SKIP]
            for f in files:
                if f.endswith(".py") and not f.startswith("test_"):
                    try:
                        path = os.path.join(root, f)
                        rel = os.path.relpath(path, directory)
                        with open(path, encoding="utf-8", errors="replace") as fh:
                            self.codes[rel] = fh.read()
                    except (OSError, UnicodeError):
                        _log.warning("Failed to read %s", path)

    def write_files(self, directory: str):
        """Persist codes and docs to disk (subdirs are created per file)."""
        import os
        os.makedirs(directory, exist_ok=True)
        for name, src in self.codes.items():
            path = os.path.join(directory, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(src)
        for name, text in self.docs.items():
            path = os.path.join(directory, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

    # ── Checkpoint (persist / restore) ──────────────────

    def save_checkpoint(self, path: str):
        """Save serializable blackboard data to a JSON checkpoint file."""
        data = {k: v for k, v in self._data.items()
                if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
        data["codes"] = dict(self.codes)
        data["docs"] = dict(self.docs)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def load_checkpoint(self, path: str):
        """Restore blackboard data from a JSON checkpoint file."""
        if not __import__("os").path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            if k in ("codes", "docs"):
                continue
            self._data[k] = v
        self.codes = data.get("codes", {})
        self.docs = data.get("docs", {})
