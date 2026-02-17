"""Tests for Multi-desktop Registration."""
from registration import generate_agent_id, detect_capabilities, AgentInfo, RegistrationManager, get_local_ip


def test_generate_agent_id_deterministic():
    id1 = generate_agent_id("my-agent")
    id2 = generate_agent_id("my-agent")
    assert id1 == id2
    assert len(id1) == 16


def test_different_labels_different_ids():
    assert generate_agent_id("agent-1") != generate_agent_id("agent-2")


def test_detect_capabilities():
    caps = detect_capabilities()
    assert "shell_executor" in caps
    assert "bash" in caps or "powershell" in caps or "cmd" in caps


def test_agent_info_to_dict():
    info = AgentInfo(agent_id="abc", label="test")
    d = info.to_dict()
    assert d["agent_id"] == "abc"
    assert d["label"] == "test"
    assert "capabilities" in d


def test_get_local_ip():
    ip = get_local_ip()
    assert isinstance(ip, str)


def test_registration_manager_build_info():
    mgr = RegistrationManager(api_url="http://localhost", api_key="test", label="my-desktop")
    info = mgr.build_agent_info()
    assert info.label == "my-desktop"
    assert info.agent_id == mgr.agent_id
    assert len(info.capabilities) > 0
