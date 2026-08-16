"""Test A3/B5 质检强化 — e2e 测试证据 + 覆盖率门槛."""
from codegen.application.phases.quality_gate import QualityGate


def _phase():
    from codegen.domain.blackboard import Blackboard
    return QualityGate(Blackboard())


def test_test_failure_appends_feature_and_downgrades():
    """测试失败 → 追加 feature 项 + PASS 降为 WARN。"""
    qg = _phase()
    result = qg._apply_evidence_gates(
        {"verdict": "PASS", "features": [], "score": 100},
        has_bugs=True, test_output="1 failed: test_x")
    assert result["verdict"] == "WARN"
    names = [f["name"] for f in result["features"]]
    assert "自动化测试通过" in names
    entry = [f for f in result["features"] if f["name"] == "自动化测试通过"][0]
    assert entry["status"] == "NO"
    assert "test_x" in entry["notes"]


def test_test_failure_keeps_fail():
    qg = _phase()
    result = qg._apply_evidence_gates(
        {"verdict": "FAIL", "features": [], "score": 10},
        has_bugs=True, test_output="boom")
    assert result["verdict"] == "FAIL"


def test_coverage_below_threshold_downgrades(monkeypatch):
    """覆盖率低于阈值（60%）→ 追加 feature + PASS 降为 WARN。"""
    qg = _phase()
    monkeypatch.setattr("codegen.application.phases.quality_gate.load_pipeline_config",
                        lambda: {"quality_gate_min_coverage": 60})
    result = qg._apply_evidence_gates(
        {"verdict": "PASS", "features": [], "score": 100},
        has_bugs=False,
        test_output="TOTAL 10 2 42%")
    assert result["verdict"] == "WARN"
    names = [f["name"] for f in result["features"]]
    assert any("覆盖率" in n for n in names)


def test_coverage_above_threshold_untouched(monkeypatch):
    qg = _phase()
    monkeypatch.setattr("codegen.application.phases.quality_gate.load_pipeline_config",
                        lambda: {"quality_gate_min_coverage": 60})
    result = qg._apply_evidence_gates(
        {"verdict": "PASS", "features": [], "score": 100},
        has_bugs=False,
        test_output="TOTAL 10 2 80%")
    assert result["verdict"] == "PASS"
    assert result["features"] == []


def test_parse_coverage():
    assert QualityGate._parse_coverage("TOTAL 120 40 67%") == 67
    assert QualityGate._parse_coverage("no cov here") is None


def test_features_null_does_not_crash():
    """P1-2：inspector 输出 features: null（json_mode 只保证合法 JSON）
    → list(None) 曾抛 TypeError run 级崩溃 → 现在兜底空列表。"""
    qg = _phase()
    result = qg._apply_evidence_gates(
        {"verdict": "PASS", "features": None, "score": 100},
        has_bugs=False, test_output="")
    assert result["verdict"] == "PASS"
    assert result["features"] == []
