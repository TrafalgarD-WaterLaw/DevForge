"""Test config loader — P1-3 未知键告警 / P1-4 配置名回退 / 键清单。"""
import logging

from core.config import load_pipeline_config


def test_unknown_config_name_falls_back_to_default(caplog):
    """不存在的 pipeline 名 → 回退 default（此前 FileNotFoundError 崩阶段）。"""
    with caplog.at_level(logging.WARNING):
        cfg = load_pipeline_config("no-such-pipeline")
    assert cfg.get("pipeline"), "回退 default 后应拿到完整配置"
    assert any("回退 default" in r.message for r in caplog.records)


def test_unknown_keys_warn(caplog):
    """拼错的配置键 → 启动告警（不再静默用默认值难排查）。"""
    from unittest.mock import patch
    with caplog.at_level(logging.WARNING):
        with patch("core.config._load_json",
                   return_value={"llm": {"max_token": 123},
                                 "bogus_section": {}}):
            load_pipeline_config()
    msgs = [r.message for r in caplog.records]
    assert any("max_token" in m for m in msgs)
    assert any("bogus_section" in m for m in msgs)


def test_known_keys_no_warning(caplog):
    with caplog.at_level(logging.WARNING):
        load_pipeline_config()
    assert not [r for r in caplog.records if "未知配置键" in r.message]


def test_named_config_inherits_default():
    """P2：iterate.json 只有 pipeline 段 —— 加载时合并 default 的 llm/tools，
    迭代模式不再静默回落默认值（与 default.json 调参脱节）。"""
    cfg = load_pipeline_config("iterate")
    assert cfg["pipeline"] == ["Iterate"]
    default = load_pipeline_config()
    assert cfg["llm"]["max_tool_rounds"] == default["llm"]["max_tool_rounds"]
    assert cfg["tools"] == default["tools"]
