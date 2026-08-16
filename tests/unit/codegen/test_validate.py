"""Test schema validation + validated_react retry."""
from unittest.mock import MagicMock

from codegen.domain.validate import validate_output, validated_react

PM_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "question": {"type": ["object", "null"]},
        "summary": {"type": ["object", "null"]},
    },
    "required": ["message", "question", "summary"],
}

REVIEWER_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "severity": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["file", "line", "severity", "description"],
            },
        },
    },
    "required": ["issues"],
}


class TestValidateOutput:
    def test_valid(self):
        assert validate_output(
            {"message": "hi", "question": None, "summary": {"a": 1}},
            PM_SCHEMA) == []

    def test_missing_required(self):
        errors = validate_output({"message": "hi"}, PM_SCHEMA)
        assert any("question" in e for e in errors)
        assert any("summary" in e for e in errors)

    def test_wrong_type(self):
        errors = validate_output(
            {"message": 42, "question": None, "summary": None}, PM_SCHEMA)
        assert any("message" in e for e in errors)

    def test_null_allowed_for_union_type(self):
        assert validate_output(
            {"message": "hi", "question": None, "summary": None}, PM_SCHEMA) == []

    def test_null_checker_rejects_wrong_type_in_union(self):
        # B5: 之前 ["object","null"] union 缺 "null" 检查器 → 任意类型都放行
        errors = validate_output(
            {"message": "hi", "question": 42, "summary": None}, PM_SCHEMA)
        assert any("question" in e for e in errors)

    def test_not_a_dict(self):
        assert validate_output([1, 2], PM_SCHEMA)


class TestNestedValidation:
    """B5: 嵌套数组项（reviewer issues）的 required 字段也参与校验。"""

    def test_nested_item_missing_required(self):
        data = {"issues": [{"file": "a.py", "line": 1,
                            "severity": "HIGH"}]}  # 缺 description
        errors = validate_output(data, REVIEWER_SCHEMA)
        assert any("description" in e for e in errors)

    def test_nested_item_wrong_type(self):
        data = {"issues": [{"file": "a.py", "line": 1,
                            "severity": 42, "description": "x"}]}  # severity 非 string
        errors = validate_output(data, REVIEWER_SCHEMA)
        assert any("severity" in e for e in errors)

    def test_nested_item_not_object(self):
        data = {"issues": ["not-an-object"]}
        errors = validate_output(data, REVIEWER_SCHEMA)
        assert errors

    def test_valid_nested_items_pass(self):
        data = {"issues": [{"file": "a.py", "line": 1,
                            "severity": "HIGH", "description": "bug"}]}
        assert validate_output(data, REVIEWER_SCHEMA) == []


class TestValidatedReact:
    def _mk_agent(self, contents):
        """Agent whose react() returns contents in order."""
        agent = MagicMock()
        idx = [0]
        def react(prompt, **kw):
            i = min(idx[0], len(contents) - 1)
            idx[0] += 1
            return contents[i]
        agent.react = MagicMock(side_effect=react)
        agent.name = "test"
        return agent

    def test_valid_first_try_no_retry(self):
        agent = self._mk_agent([
            {"message": "ok", "question": None, "summary": {"x": 1}},
        ])
        result = validated_react(agent, "prompt", PM_SCHEMA)
        assert result["message"] == "ok"

    def test_invalid_then_retry(self):
        agent = self._mk_agent([
            {"message": "bad"},                      # missing question/summary
            {"message": "fixed", "question": None, "summary": {"x": 1}},
        ])
        result = validated_react(agent, "prompt", PM_SCHEMA)
        assert result["message"] == "fixed"
        assert agent.react.call_count == 2

    def test_final_invalid_returns_last(self):
        agent = self._mk_agent([
            {"message": "bad1"},
            {"message": "bad2"},
        ])
        result = validated_react(agent, "prompt", PM_SCHEMA, retries=1)
        assert result["message"] == "bad2"
        assert agent.react.call_count == 2

    def test_reviewer_schema(self):
        agent = self._mk_agent([
            {"issues": [{"file": "a.py", "line": 1,
                         "severity": "HIGH", "description": "bug"}]},
        ])
        result = validated_react(agent, "prompt", REVIEWER_SCHEMA)
        assert len(result["issues"]) == 1


def test_reviewer_line_as_string_passes():
    """P0-3: 模型输出字符串行号 "3" → 不再导致整份 issues 校验失败。"""
    import json
    from pathlib import Path
    schema = json.loads(Path("configs/schemas/reviewer.json")
                        .read_text(encoding="utf-8"))
    out = {"issues": [{"file": "a.py", "line": "3",
                       "severity": "HIGH", "description": "bug"}]}
    assert validate_output(out, schema) == []
    # null 行号仍合法
    out2 = {"issues": [{"file": "b.py", "line": None,
                        "severity": "LOW", "description": "nit"}]}
    assert validate_output(out2, schema) == []
