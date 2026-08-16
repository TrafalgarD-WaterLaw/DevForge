"""Test Blackboard — contract management + module graph."""
from codegen.domain.blackboard import Blackboard


def test_blackboard_contract_publish():
    bb = Blackboard()
    c = bb.publish_contract(
        module="auth",
        exports=[{"name": "login", "signature": "def login(user, pwd) -> Token"}],
        dependencies=["database"],
        author="CTO",
    )
    assert c.module == "auth"
    assert c.version == 1
    assert bb.get_contract("auth") is not None
    # Re-publish bumps version
    bb.publish_contract(module="auth", exports=[], dependencies=[])
    assert bb.get_contract("auth").version == 2


def test_blackboard_module_graph():
    bb = Blackboard()
    bb.set_module_graph({"auth": [], "dashboard": ["auth"], "api": ["auth"]})
    downstream = bb.get_downstream_modules("auth")
    assert "dashboard" in downstream
    assert "api" in downstream
    assert bb.get_downstream_modules("api") == []
