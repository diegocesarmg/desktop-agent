"""Tests for Mission Manager."""
import pytest
from mission_manager import MissionManager, MissionStatus, CommandRecord


class TestMissionCRUD:
    @pytest.fixture
    def mgr(self, tmp_path):
        return MissionManager(storage_path=str(tmp_path / "data"))

    def test_create(self, mgr):
        m = mgr.create("Test Mission", description="desc")
        assert m.name == "Test Mission"
        assert m.status == MissionStatus.PENDING

    def test_get(self, mgr):
        m = mgr.create("M1")
        assert mgr.get(m.id) is not None
        assert mgr.get("nonexistent") is None

    def test_list(self, mgr):
        mgr.create("A")
        mgr.create("B")
        assert len(mgr.list_missions()) == 2

    def test_update(self, mgr):
        m = mgr.create("M1")
        mgr.update(m.id, name="Updated")
        assert mgr.get(m.id).name == "Updated"

    def test_delete(self, mgr):
        m = mgr.create("M1")
        assert mgr.delete(m.id)
        assert mgr.get(m.id) is None

    def test_complete(self, mgr):
        m = mgr.create("M1")
        mgr.complete(m.id)
        assert mgr.get(m.id).status == MissionStatus.COMPLETED

    def test_yolo_mode(self, mgr):
        m = mgr.create("M1", yolo=True)
        from mission_manager import ExecutionMode
        assert m.execution_mode == ExecutionMode.YOLO

    def test_track_command(self, mgr):
        m = mgr.create("M1")
        rec = CommandRecord(id="c1", command="echo hi", status="completed", exit_code=0)
        assert mgr.track_command(m.id, rec)
        history = mgr.get_command_history(m.id)
        assert len(history) == 1

    def test_stats(self, mgr):
        m = mgr.create("M1")
        mgr.track_command(m.id, CommandRecord(id="c1", command="echo", status="completed", duration_ms=100))
        mgr.track_command(m.id, CommandRecord(id="c2", command="fail", status="failed", duration_ms=50))
        stats = mgr.get_mission_stats(m.id)
        assert stats["total_commands"] == 2
        assert stats["completed"] == 1
        assert stats["failed"] == 1

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "data")
        mgr1 = MissionManager(storage_path=path)
        m = mgr1.create("Persist")
        mid = m.id
        mgr2 = MissionManager(storage_path=path)
        assert mgr2.get(mid) is not None
        assert mgr2.get(mid).name == "Persist"
