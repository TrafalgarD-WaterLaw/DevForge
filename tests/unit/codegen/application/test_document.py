"""Test Documentation — 文档 agent 流式输出（过程实时可见）。"""
from unittest.mock import MagicMock

from codegen.domain.blackboard import Blackboard

import codegen.application.phases.documentation as doc_mod
from codegen.application.phases.documentation import Documentation


def test_doc_tasks_stream(monkeypatch):
    """文档任务必须流式（json_mode=False, stream=True）—— 此前 json_mode
    单次调用零事件，前端面板全程"0 个子 agent"、无过程可见。"""
    captured = {}

    def fake_parallel(tasks):
        captured["tasks"] = tasks
        return []

    monkeypatch.setattr(doc_mod, "parallel", fake_parallel)
    bb = Blackboard()
    bb["directory"] = ""
    bb["codes"] = {"a.py": "print(1)"}
    Documentation(bb).run()

    tasks = captured["tasks"]
    assert len(tasks) == 2
    # (agent, prompt, json_mode=False, stream=True)
    assert all(len(t) == 4 and t[2] is False and t[3] is True for t in tasks)


def test_doc_plain_text_lands_in_docs(monkeypatch):
    """纯文本输出（无 JSON 包裹）→ _parse 返回 {"message": text}，
    下游 blackboard.docs 行为与 JSON 输出一致。"""
    from codegen.domain.agent import Agent

    texts = {
        "dependency_analyst": "# No external dependencies.\n",
        "technical_writer": "## Introduction\n记账本工具。",
    }

    def fake_parallel(tasks):
        return [(a, Agent._parse(texts[a.name])) for a, *_ in tasks]

    monkeypatch.setattr(doc_mod, "parallel", fake_parallel)
    bb = Blackboard()
    bb["directory"] = ""
    bb["codes"] = {"a.py": "print(1)"}
    monkeypatch.setattr(bb, "write_files", lambda d: None)  # 不真写盘
    Documentation(bb).run()

    assert bb.docs["requirements.txt"].startswith("# No external")
    assert "Introduction" in bb.docs["manual.md"]


def test_dependency_analyst_message_sanitized(monkeypatch):
    """H5: 依赖分析师输出尾部散文碎片（"dependencies."）被清洗，
    不污染 requirements.txt。"""
    def fake_parallel(tasks):
        return [(a, {"message": "# No external dependencies.\ndependencies."})
                for a, *_ in tasks]

    monkeypatch.setattr(doc_mod, "parallel", fake_parallel)
    bb = Blackboard()
    bb["directory"] = ""
    bb["codes"] = {"a.py": "print(1)"}
    monkeypatch.setattr(bb, "write_files", lambda d: None)
    Documentation(bb).run()

    assert bb.docs["requirements.txt"] == "# No external dependencies."
    # 正常包名行不被误删
    def fake_parallel2(tasks):
        return [(a, {"message": "requests==2.31.0\n# comment"}) for a, *_ in tasks]
    monkeypatch.setattr(doc_mod, "parallel", fake_parallel2)
    Documentation(bb).run()
    assert bb.docs["requirements.txt"] == "requests==2.31.0\n# comment"


def test_preserve_pinned_requirements(monkeypatch, tmp_path):
    """P1-6：Coding._auto_install 写回的 ==pin 不被 analyst 无 pin 清单覆盖。
    盘上已有 pin 的同名包沿用 pin，analyst 只增补新包。"""
    from codegen.application.phases.documentation import Documentation

    bb = Blackboard()
    # analyst 输出：requests 无 pin、新增 click、注释保留
    bb.docs["requirements.txt"] = "requests\n# comment\nclick\n"
    (tmp_path / "requirements.txt").write_text(
        "requests==2.31.0\n", encoding="utf-8")

    Documentation(bb)._preserve_pinned_requirements(str(tmp_path))

    text = bb.docs["requirements.txt"]
    assert "requests==2.31.0" in text      # 盘上 pin 保留
    assert "\nrequests\n" not in "\n" + text   # 无 pin 版本被替换
    assert "click" in text                 # 新包增补
    assert "# comment" in text             # 注释行原样保留


def test_preserve_pinned_requirements_no_disk_pins(tmp_path):
    """盘上无 requirements.txt → 合并是 no-op（analyst 输出原样）。"""
    from codegen.application.phases.documentation import Documentation

    bb = Blackboard()
    bb.docs["requirements.txt"] = "requests\n"
    Documentation(bb)._preserve_pinned_requirements(str(tmp_path))
    assert bb.docs["requirements.txt"] == "requests\n"
