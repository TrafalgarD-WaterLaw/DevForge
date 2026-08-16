"""Memory data model."""
from dataclasses import dataclass, field

@dataclass
class MemoryEntry:
    id: str
    project: str
    phase: str
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    detail: str = ""
    timestamp: float = 0.0
    # 内容指纹（写入时由 MemoryStore 计算并存入 metadata，用于跨任务去重）
    metadata_fingerprint: str = ""
