"""Test Design coverage check."""
import pytest

from codegen.domain.blackboard import Blackboard
from codegen.application.phases.design import Design


@pytest.fixture
def bb():
    bb = Blackboard()
    bb["requirements"] = {
        "project_name": "wc",
        "core_features": ["count lines", "count words", "show help"],
    }
    return bb


class TestCoverageCheck:
    def test_all_covered(self, bb):
        d = Design(bb)
        design = {"modules": [
            {"name": "counter",
             "purpose": "count lines, count words, show help",
             "exports": []},
        ]}
        assert d._check_coverage(design) == []

    def test_missing_feature(self, bb):
        d = Design(bb)
        design = {"modules": [
            {"name": "counter", "purpose": "count lines and words", "exports": []},
        ]}
        missing = d._check_coverage(design)
        assert "show help" in missing
        assert "count lines" not in missing

    def test_no_requirements_no_gap(self, bb):
        bb["requirements"] = {}
        d = Design(bb)
        assert d._check_coverage({"modules": []}) == []

    def test_english_case_insensitive(self, bb):
        bb["requirements"]["core_features"] = ["COUNT LINES"]
        d = Design(bb)
        design = {"modules": [
            {"name": "Counter", "purpose": "count lines and words", "exports": []},
        ]}
        assert d._check_coverage(design) == []

    def test_export_description_counts(self, bb):
        bb["requirements"]["core_features"] = ["help"]
        d = Design(bb)
        design = {"modules": [
            {"name": "cli", "purpose": "entry",
             "exports": [{"description": "show help text"}]},
        ]}
        assert d._check_coverage(design) == []

    def test_word_boundary_no_substring_match(self, bb):
        """B14: 'count' 不能靠 'counter' 满足；'cli' 不能靠 'client' 满足。"""
        bb["requirements"]["core_features"] = ["count", "cli"]
        d = Design(bb)
        design = {"modules": [
            {"name": "counter",
             "purpose": "word counter utility for client projects",
             "exports": []},
        ]}
        missing = d._check_coverage(design)
        assert missing == ["count", "cli"]


def test_coverage_short_word_substring():
    """审阅修复：短词（≤2 字）用子串匹配 —— '报表' 被 '报表功能' 满足。"""
    from codegen.domain.blackboard import Blackboard
    from codegen.application.phases.design import Design
    bb = Blackboard()
    bb["requirements"] = {"core_features": ["报表", "提醒"]}
    d = Design(bb)
    modules = [{"name": "report", "purpose": "生成月度报表功能"},
               {"name": "alert", "purpose": "预算提醒"}]
    blob = " ".join([m["name"] for m in modules]
                    + [m["purpose"] for m in modules]).lower()
    missing = d._check_coverage({"modules": modules})
    assert missing == []


def test_coverage_long_word_still_boundary():
    """长词保持词边界：'count' 不能被 'counter' 满足（B14 不回退）。"""
    from codegen.domain.blackboard import Blackboard
    from codegen.application.phases.design import Design
    bb = Blackboard()
    bb["requirements"] = {"core_features": ["count"]}
    d = Design(bb)
    modules = [{"name": "counter", "purpose": "计数工具"}]
    assert d._check_coverage({"modules": modules}) == ["count"]


def test_coverage_cjk_multi_char_substring():
    """P1-4：3+ 字中文特性在短语中部也子串匹配 —— \b 在 CJK 字符间
    不产生边界，此前 "批量处理" vs "批量处理文件" 必然误报缺失。"""
    from codegen.domain.blackboard import Blackboard
    from codegen.application.phases.design import Design
    bb = Blackboard()
    bb["requirements"] = {"core_features": ["批量处理", "月度汇总"]}
    d = Design(bb)
    modules = [{"name": "batch", "purpose": "批量处理文件"},
               {"name": "report", "purpose": "生成月度汇总报告"}]
    assert d._check_coverage({"modules": modules}) == []
