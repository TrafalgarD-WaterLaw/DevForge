"""Design — CTO + CPO design the complete product architecture."""

import json
import re
from core.events import Events, HookRegistry
from codegen.application.patterns import converse
from codegen.domain.phase import Phase
from codegen.domain.registry import register_phase
from codegen.domain.validate import validated_react

MAX_COVERAGE_RETRIES = 2

@register_phase
class Design(Phase):
    """CTO + CPO discuss, then CTO submits structured architecture."""

    def run(self):
        cto = self.agent("chief_technology_officer")
        cpo = self.agent("chief_product_officer")
        feedback = self.blackboard.get("user_feedback", []) or []
        speaker_prompt = self.prompt("chief_technology_officer")
        if feedback:
            speaker_prompt += (
                "\n\nUSER FEEDBACK（用户追加的需求/修改意见，必须纳入本次设计）:\n- "
                + "\n- ".join((str(f) for f in feedback))
            )
            self.blackboard["user_feedback"] = []
        converse(
            speaker=cto,
            listener=cpo,
            speaker_prompt=speaker_prompt,
            listener_prompt=self.prompt("chief_product_officer"),
            stream=True,
        )
        design = validated_react(
            cto,
            f"Output the final architecture JSON matching this schema:\n{json.dumps(self.schema('chief_technology_officer'))}\n\nThe CPO agreed.",
            self.schema("chief_technology_officer"),
        )
        for attempt in range(MAX_COVERAGE_RETRIES + 1):
            missing = self._check_coverage(design)
            self.blackboard["coverage_check"] = {"missing": missing, "attempt": attempt}
            if not missing:
                break
            print(
                f"  [Design] coverage gap ({missing}) — CTO re-designing (attempt {attempt + 1})",
                flush=True,
            )
            design = validated_react(
                cto,
                f"Your architecture misses these customer requirements: {', '.join(missing)}.\nAdd or adjust modules so every requirement is covered.\nOutput the full architecture JSON again matching the schema.",
                self.schema("chief_technology_officer"),
            )
        print(
            f"  [CTO Final] → {len(json.dumps(design, ensure_ascii=False))} chars",
            flush=True,
        )
        HookRegistry.trigger(
            Events.CONVERSATION_TURN,
            agent="chief_technology_officer",
            content=json.dumps(design, ensure_ascii=False),
            turn=-1,
        )
        self._apply_design(design)
        HookRegistry.trigger(
            "design_submitted",
            modality=design.get("modality", ""),
            language=design.get("language", ""),
            modules=design.get("modules", []),
        )

    def _check_coverage(self, design_data: dict) -> list[str]:
        """Return core features with no module referencing them."""
        req = self.blackboard.get("requirements", {})
        features = req.get("core_features", [])
        if not features:
            return []
        modules = design_data.get("modules", [])
        blob = " ".join(
            [m.get("name", "") for m in modules]
            + [m.get("purpose", "") for m in modules]
            + [e.get("description", "") for m in modules for e in m.get("exports", [])]
        ).lower()
        missing = []
        for f in features:
            key = f.strip().lower()
            if not key:
                continue
            # 中文 feature 不参与覆盖检查：设计/模块名是英文，中文串在
            # blob 里必然不命中 → 之前会让 CTO 收到虚假 missing 白重试
            # 2 次（且永远修不好）。中英同义映射不现实，跳过。
            if any(("一" <= ch <= "鿿" for ch in key)):
                continue
            if len(key) <= 2:
                hit = key in blob
            else:
                hit = bool(re.search(f"\\b{re.escape(key)}\\b", blob))
            if not hit:
                missing.append(f)
        return missing

    def _apply_design(self, design_data: dict):
        self.blackboard["modality"] = design_data.get("modality", "").lower()
        self.blackboard["language"] = design_data.get("language", "")
        raw_modules = design_data.get("modules", [])
        if not raw_modules:
            raw_modules = [
                {"name": "main", "purpose": self.blackboard.get("task_prompt", "")[:80]}
            ]
        modules = []
        for m in raw_modules:
            modules.append(
                {
                    "name": m.get("name", "main"),
                    "description": m.get("purpose", m.get("description", "")),
                    "exports": m.get("exports", []),
                    "depends_on": m.get("depends_on", []),
                    "files": m.get("files", []),
                }
            )
        self.blackboard["modules"] = modules
        graph = {}
        for m in modules:
            self.blackboard.publish_contract(
                module=m["name"],
                exports=m["exports"],
                dependencies=m.get("depends_on", []),
                author="Design",
            )
            graph[m["name"]] = m.get("depends_on", [])
        self.blackboard.set_module_graph(graph)
        deps_of = {d for m in modules for d in m.get("depends_on", [])}
        for m in reversed(modules):
            if m["name"] not in deps_of and m.get("files"):
                entry = m.get("entry_file", "")
                self.blackboard["entry_point"] = entry or m["files"][0]
                break
