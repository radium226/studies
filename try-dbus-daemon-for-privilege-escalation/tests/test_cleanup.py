"""
Unit tests for the run cleanup mechanism.
"""
import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock

from radium226.run.server.app import (
    CleanupConfig,
    RunCleanupService,
    RunInterface,
    CommandExecutorInterface
)


class TestCleanupConfig:
    """Test the cleanup configuration dataclass"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = CleanupConfig()
        assert config.max_age_hours == 24
        assert config.max_completed_runs == 100
        assert config.max_total_runs == 500
        assert config.cleanup_interval_minutes == 60
        assert config.keep_running is True
    
    def test_custom_config(self):
        """Test custom configuration values"""
        config = CleanupConfig(
            max_age_hours=12,
            max_completed_runs=50,
            max_total_runs=200,
            cleanup_interval_minutes=30,
            keep_running=False
        )
        assert config.max_age_hours == 12
        assert config.max_completed_runs == 50
        assert config.max_total_runs == 200
        assert config.cleanup_interval_minutes == 30
        assert config.keep_running is False


class TestRunCleanupService:
    """Test the RunCleanupService class"""
    
    @pytest.fixture
    def config(self):
        """Default cleanup configuration for testing"""
        return CleanupConfig(
            max_age_hours=2,
            max_completed_runs=3,
            max_total_runs=5,
            cleanup_interval_minutes=1,
            keep_running=True
        )
    
    @pytest.fixture
    def cleanup_service(self, config):
        """Cleanup service with test configuration"""
        return RunCleanupService(config)
    
    @pytest.fixture
    def mock_bus(self):
        """Mock D-Bus for testing"""
        bus = Mock()
        bus.unexport = Mock()
        return bus
    
    def create_mock_run(self, execution_id: str, status: str, start_time: datetime, sequence_number: int = 1):
        """Create a mock run for testing"""
        run = Mock(spec=RunInterface)
        run.execution_id = execution_id
        run.status = status
        run.start_time = start_time
        run.sequence_number = sequence_number
        return run
    
    def test_cleanup_no_runs(self, cleanup_service, mock_bus):
        """Test cleanup with no runs"""
        runs_dict = {}
        cleaned_count = cleanup_service.cleanup_runs(runs_dict, mock_bus)
        assert cleaned_count == 0
        assert len(runs_dict) == 0
    
    def test_cleanup_age_based(self, cleanup_service, mock_bus):
        """Test age-based cleanup"""
        now = datetime.now()
        old_time = now - timedelta(hours=3)  # Older than max_age_hours (2)
        new_time = now - timedelta(hours=1)  # Newer than max_age_hours
        
        runs_dict = {
            "old-run": self.create_mock_run("old-run", "completed", old_time, 1),
            "new-run": self.create_mock_run("new-run", "completed", new_time, 2),
        }
        
        cleaned_count = cleanup_service.cleanup_runs(runs_dict, mock_bus)
        assert cleaned_count == 1
        assert "old-run" not in runs_dict
        assert "new-run" in runs_dict
        
        # Verify D-Bus unexport was called
        mock_bus.unexport.assert_called_once_with("/com/radium226/Run/old_run")
    
    def test_cleanup_keep_running(self, cleanup_service, mock_bus):
        """Test that running processes are not cleaned up when keep_running=True"""
        now = datetime.now()
        old_time = now - timedelta(hours=3)  # Older than max_age_hours
        
        runs_dict = {
            "old-running": self.create_mock_run("old-running", "running", old_time, 1),
            "old-completed": self.create_mock_run("old-completed", "completed", old_time, 2),
        }
        
        cleaned_count = cleanup_service.cleanup_runs(runs_dict, mock_bus)
        assert cleaned_count == 1
        assert "old-running" in runs_dict  # Running process kept
        assert "old-completed" not in runs_dict  # Completed process removed
    
    def test_cleanup_count_based_completed(self, cleanup_service, mock_bus):
        """Test count-based cleanup for completed runs"""
        now = datetime.now()
        recent_time = now - timedelta(minutes=30)  # Recent, not age-based cleanup
        
        # Create 5 completed runs (more than max_completed_runs=3)
        runs_dict = {}
        for i in range(5):
            run_id = f"run-{i}"
            runs_dict[run_id] = self.create_mock_run(
                run_id, "completed", recent_time - timedelta(minutes=i), i
            )
        
        cleaned_count = cleanup_service.cleanup_runs(runs_dict, mock_bus)
        assert cleaned_count == 2  # Should remove 2 oldest completed runs
        assert len(runs_dict) == 3  # Keep 3 completed runs
        
        # The oldest runs (0 and 1) should be removed
        assert "run-0" not in runs_dict
        assert "run-1" not in runs_dict
        assert "run-2" in runs_dict
        assert "run-3" in runs_dict
        assert "run-4" in runs_dict
    
    def test_cleanup_count_based_total(self, cleanup_service, mock_bus):
        """Test count-based cleanup for total runs"""
        now = datetime.now()
        recent_time = now - timedelta(minutes=30)  # Recent, not age-based cleanup
        
        # Create 7 runs (more than max_total_runs=5), mix of completed and running
        runs_dict = {}
        for i in range(7):
            run_id = f"run-{i}"
            status = "running" if i >= 5 else "completed"  # Last 2 are running
            runs_dict[run_id] = self.create_mock_run(
                run_id, status, recent_time - timedelta(minutes=i), i
            )
        
        cleaned_count = cleanup_service.cleanup_runs(runs_dict, mock_bus)
        
        # Should remove oldest non-running runs to get to max_total_runs
        # With keep_running=True, only completed runs can be removed
        # We have 5 completed runs (0-4), need to remove 2 to get total to 5
        assert cleaned_count == 2
        assert len(runs_dict) == 5
        
        # Check that running processes are kept
        assert "run-5" in runs_dict  # running
        assert "run-6" in runs_dict  # running
    
    def test_cleanup_mixed_policies(self, cleanup_service, mock_bus):
        """Test cleanup with multiple policies applying"""
        now = datetime.now()
        old_time = now - timedelta(hours=3)  # Triggers age-based cleanup
        recent_time = now - timedelta(minutes=30)
        
        runs_dict = {
            # These should be removed by age
            "old-1": self.create_mock_run("old-1", "completed", old_time, 1),
            "old-2": self.create_mock_run("old-2", "error", old_time, 2),
            
            # These are recent but might be removed by count
            "recent-1": self.create_mock_run("recent-1", "completed", recent_time, 3),
            "recent-2": self.create_mock_run("recent-2", "completed", recent_time, 4),
            "recent-3": self.create_mock_run("recent-3", "completed", recent_time, 5),
            "recent-4": self.create_mock_run("recent-4", "completed", recent_time, 6),
        }
        
        cleaned_count = cleanup_service.cleanup_runs(runs_dict, mock_bus)
        
        # Age-based: 2 old runs removed
        # Count-based: excess completed runs removed to get to max_completed_runs=3
        assert cleaned_count >= 2  # At least the age-based removals
        assert "old-1" not in runs_dict
        assert "old-2" not in runs_dict
    
    def test_cleanup_with_dbus_unexport_error(self, cleanup_service, mock_bus):
        """Test cleanup handles D-Bus unexport errors gracefully"""
        mock_bus.unexport.side_effect = Exception("D-Bus error")
        
        now = datetime.now()
        old_time = now - timedelta(hours=3)
        
        runs_dict = {
            "old-run": self.create_mock_run("old-run", "completed", old_time, 1),
        }
        
        # Should not raise exception and should still remove from dict
        cleaned_count = cleanup_service.cleanup_runs(runs_dict, mock_bus)
        assert cleaned_count == 1
        assert "old-run" not in runs_dict
    
    @pytest.mark.asyncio
    async def test_automatic_cleanup_start_stop(self, cleanup_service, mock_bus):
        """Test starting and stopping automatic cleanup"""
        runs_dict = {}
        
        # Start automatic cleanup
        cleanup_service.start_automatic_cleanup(runs_dict, mock_bus)
        assert cleanup_service._cleanup_task is not None
        assert not cleanup_service._cleanup_task.done()
        
        # Stop automatic cleanup
        cleanup_service.stop_automatic_cleanup()
        
        # Give it a moment to cancel
        await asyncio.sleep(0.1)
        assert cleanup_service._cleanup_task.cancelled()


class TestCommandExecutorInterfaceCleanup:
    """Test cleanup integration with CommandExecutorInterface"""
    
    @pytest.fixture
    def mock_bus(self):
        """Mock D-Bus for testing"""
        bus = Mock()
        bus.export = Mock()
        bus.unexport = Mock()
        return bus
    
    @pytest.fixture
    def config(self):
        """Test cleanup configuration"""
        return CleanupConfig(
            max_age_hours=1,
            max_completed_runs=2,
            max_total_runs=3,
            cleanup_interval_minutes=1,
            keep_running=True
        )
    
    @pytest.fixture
    def executor(self, mock_bus, config):
        """CommandExecutorInterface with test configuration"""
        executor = CommandExecutorInterface(mock_bus, config)
        # Stop automatic cleanup for controlled testing
        executor.cleanup_service.stop_automatic_cleanup()
        return executor
    
    def create_test_run(self, executor, execution_id: str, status: str = "completed"):
        """Create a test run in the executor"""
        run = Mock()
        run.execution_id = execution_id
        run.status = status
        run.start_time = datetime.now()
        run.sequence_number = len(executor.runs) + 1
        executor.runs[execution_id] = run
        return run
    
    def test_manual_cleanup_dbus_method(self, executor):
        """Test the CleanupOldRuns D-Bus method"""
        # Create some test runs
        old_time = datetime.now() - timedelta(hours=2)
        
        run1 = self.create_test_run(executor, "run-1")
        run1.start_time = old_time  # Make it old
        
        run2 = self.create_test_run(executor, "run-2")
        
        # Call the D-Bus method
        cleaned_count = executor.CleanupOldRuns()
        
        assert cleaned_count == 1
        assert "run-1" not in executor.runs
        assert "run-2" in executor.runs
    
    def test_get_cleanup_stats_dbus_method(self, executor):
        """Test the GetCleanupStats D-Bus method"""
        # Create test runs with different statuses
        self.create_test_run(executor, "run-1", "completed")
        self.create_test_run(executor, "run-2", "running")
        self.create_test_run(executor, "run-3", "error")
        
        stats = executor.GetCleanupStats()
        
        assert stats["total_runs"].value == 3
        assert stats["completed_runs"].value == 1
        assert stats["running_runs"].value == 1
        assert stats["error_runs"].value == 1
        assert stats["aborted_runs"].value == 0
        assert stats["max_age_hours"].value == 1
        assert stats["max_completed_runs"].value == 2
        assert stats["max_total_runs"].value == 3
    
    def test_remove_run_dbus_method(self, executor):
        """Test the RemoveRun D-Bus method"""
        # Create a completed run
        self.create_test_run(executor, "run-1", "completed")
        
        # Remove it
        result = executor.RemoveRun("run-1")
        assert result is True
        assert "run-1" not in executor.runs
        
        # Try to remove non-existent run
        result = executor.RemoveRun("non-existent")
        assert result is False
    
    def test_remove_running_process_safety(self, executor):
        """Test that running processes cannot be manually removed"""
        # Create a running process
        self.create_test_run(executor, "running-1", "running")
        
        # Try to remove it
        result = executor.RemoveRun("running-1")
        assert result is False
        assert "running-1" in executor.runs  # Should still exist
    
    def test_cleanup_config_initialization(self, mock_bus):
        """Test that cleanup configuration is properly initialized"""
        # Test with default config
        executor1 = CommandExecutorInterface(mock_bus)
        assert executor1.cleanup_config.max_age_hours == 24
        
        # Test with custom config
        custom_config = CleanupConfig(max_age_hours=12)
        executor2 = CommandExecutorInterface(mock_bus, custom_config)
        assert executor2.cleanup_config.max_age_hours == 12


@pytest.mark.asyncio
class TestCleanupIntegration:
    """Integration tests for cleanup functionality"""
    
    async def test_cleanup_during_execution(self):
        """Test that cleanup works during normal operation"""
        # This would require a more complex integration test
        # with actual D-Bus setup, which is better suited for e2e tests
        pass
    
    async def test_cleanup_performance(self):
        """Test cleanup performance with many runs"""
        config = CleanupConfig(
            max_age_hours=1,
            max_completed_runs=100,
            max_total_runs=1000
        )
        service = RunCleanupService(config)
        mock_bus = Mock()
        
        # Create many runs
        runs_dict = {}
        now = datetime.now()
        
        for i in range(1500):  # More than max_total_runs
            run_id = f"run-{i}"
            # Make older runs to trigger cleanup
            start_time = now - timedelta(hours=2 if i < 500 else 0)
            run = Mock()
            run.execution_id = run_id
            run.status = "completed"
            run.start_time = start_time
            run.sequence_number = i
            runs_dict[run_id] = run
        
        # Measure cleanup performance
        import time
        start = time.time()
        cleaned_count = service.cleanup_runs(runs_dict, mock_bus)
        duration = time.time() - start
        
        # Should clean up excess runs efficiently
        assert cleaned_count > 0
        assert len(runs_dict) <= config.max_total_runs
        assert duration < 1.0  # Should complete in under 1 second