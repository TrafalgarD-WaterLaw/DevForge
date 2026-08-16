"""Documentation — parallel generation of dependency file + user manual."""

import json
import os
from core.events import Events, HookRegistry
from codegen.application.patterns import parallel
from codegen.domain.phase import Phase
from codegen.domain.registry import register_phase

@register_phase
class Documentation(Phase):
    """Dependency Analyst and Technical Writer run in parallel."""

    def run(self):
        doc_of = {
            "dependency_analyst": "requirements.txt",
            "technical_writer": "manual.md",
        }
        tasks = [
            (
                self.agent("dependency_analyst"),
                self.prompt("dependency_analyst", codes=self.codes),
                False,
                True,
            ),
            (
                self.agent("technical_writer"),
                self.prompt("technical_writer", codes=self.codes),
                False,
                True,
            ),
        ]
        for agent, doc_output in parallel(tasks):
            if doc_output is None:
                continue
            msg = doc_output.get("message") or ""
            if agent.name == "dependency_analyst":
                kept = []
                for line in msg.splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        kept.append(line)
                    elif stripped.endswith("."):
                        continue
                    else:
                        kept.append(line)
                msg = "\n".join(kept)
            self.blackboard.docs[doc_of[agent.name]] = msg
        directory = self.blackboard.get("directory", "")
        if directory:
            self._preserve_pinned_requirements(directory)
            self.blackboard.write_files(directory)
        HookRegistry.trigger(
            Events.CONVERSATION_TURN,
            agent="Documentation",
            content=json.dumps({"message": f"文档完成: {', '.join(doc_of.values())}"}),
            turn=0,
        )

    def _preserve_pinned_requirements(self, directory: str):
        """P1-6: analyst 的 requirements 清单与盘上 pin 合并。

        Coding._auto_install 把实际安装版本的 ==pin 写回 requirements.txt
        （供应链锁定，C6）；analyst 基于代码 import 扫描输出无 pin 清单，
        直接覆盖会让锁定每次完整运行都被静默撤销。同包名沿用盘上 pin，
        analyst 只负责增补新包。
        """
        req_path = os.path.join(directory, "requirements.txt")
        if "requirements.txt" not in self.blackboard.docs:
            return
        pinned: dict[str, str] = {}
        if os.path.exists(req_path):
            try:
                with open(req_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if "==" in line and (not line.startswith("#")):
                            pinned[line.split("==", 1)[0].strip()] = line
            except OSError:
                pinned = {}
        if not pinned:
            return
        merged: list[str] = []
        seen: set[str] = set()
        for line in self.blackboard.docs["requirements.txt"].splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                merged.append(line)
                continue
            name = s.split("==", 1)[0].strip()
            if name in pinned:
                merged.append(pinned[name])
                seen.add(name)
            elif name not in seen:
                merged.append(line)
                seen.add(name)
        self.blackboard.docs["requirements.txt"] = "\n".join(merged) + "\n"
