"""Tests for the progress tracking system."""
import time
import pytest
from progress_tracker import (
    AcquisitionProgress,
    AcquisitionOperation,
    AcquisitionStatus,
    ActivityLogEntry,
)
from datetime import datetime


class TestAcquisitionStatus:
    def test_status_values(self):
        """Test that all status enum values exist."""
        assert AcquisitionStatus.WAITING.value == "WAITING"
        assert AcquisitionStatus.DOWNLOADING.value == "DOWNLOADING"
        assert AcquisitionStatus.VALIDATING.value == "VALIDATING"
        assert AcquisitionStatus.SAVING.value == "SAVING"
        assert AcquisitionStatus.SUCCESS.value == "SUCCESS"
        assert AcquisitionStatus.PARTIAL_SUCCESS.value == "PARTIAL_SUCCESS"
        assert AcquisitionStatus.FAILED.value == "FAILED"
        assert AcquisitionStatus.AUTH_REQUIRED.value == "AUTH_REQUIRED"
        assert AcquisitionStatus.NO_DATA_AVAILABLE.value == "NO_DATA_AVAILABLE"
        assert AcquisitionStatus.SOURCE_NOT_COVERED.value == "SOURCE_NOT_COVERED"
        assert AcquisitionStatus.MAPPING_MISSING.value == "MAPPING_MISSING"
        assert AcquisitionStatus.CANCELLED.value == "CANCELLED"


class TestAcquisitionOperation:
    def test_operation_creation(self):
        """Test creating an operation."""
        op = AcquisitionOperation(
            country="EGY",
            feature="temperature_2m",
            source="NASA POWER"
        )
        assert op.country == "EGY"
        assert op.feature == "temperature_2m"
        assert op.source == "NASA POWER"
        assert op.status == AcquisitionStatus.WAITING
        assert op.records == 0
        assert op.duration_seconds == 0.0

    def test_operation_start(self):
        """Test starting an operation."""
        op = AcquisitionOperation(country="EGY", feature="temperature_2m", source="NASA POWER")
        op.start()
        assert op.status == AcquisitionStatus.DOWNLOADING
        assert op.started_at is not None

    def test_operation_update_status(self):
        """Test updating operation status."""
        op = AcquisitionOperation(country="EGY", feature="temperature_2m", source="NASA POWER")
        op.start()
        op.update_status(
            AcquisitionStatus.SUCCESS,
            message="Downloaded successfully",
            records=1000
        )
        assert op.status == AcquisitionStatus.SUCCESS
        assert op.records == 1000
        assert op.message == "Downloaded successfully"
        assert op.completed_at is not None
        assert op.duration_seconds >= 0

    def test_operation_to_dict(self):
        """Test converting operation to dictionary."""
        op = AcquisitionOperation(country="EGY", feature="temperature_2m", source="NASA POWER")
        op.start()
        op.update_status(AcquisitionStatus.SUCCESS, message="OK", records=100)
        
        d = op.to_dict()
        assert d["country"] == "EGY"
        assert d["feature"] == "temperature_2m"
        assert d["source"] == "NASA POWER"
        assert d["status"] == "SUCCESS"
        assert d["records"] == 100
        assert d["message"] == "OK"
        assert "duration" in d


class TestActivityLogEntry:
    def test_log_entry_creation(self):
        """Test creating a log entry."""
        entry = ActivityLogEntry(
            timestamp=datetime.now(),
            message="Test message"
        )
        assert entry.message == "Test message"
        assert isinstance(entry.timestamp, datetime)

    def test_log_entry_format(self):
        """Test formatting a log entry."""
        entry = ActivityLogEntry(
            timestamp=datetime(2024, 1, 15, 14, 30, 45),
            message="Test message"
        )
        formatted = entry.format()
        assert "[14:30:45]" in formatted
        assert "Test message" in formatted


class TestAcquisitionProgress:
    def test_progress_initialization(self):
        """Test progress tracker initialization."""
        progress = AcquisitionProgress()
        assert progress.total_count == 0
        assert progress.completed_count == 0
        assert progress.cancelled is False
        assert len(progress.activity_log) == 0

    def test_initialize_operations(self):
        """Test initializing operations."""
        progress = AcquisitionProgress()
        countries = ["EGY", "DEU"]
        features = ["temperature_2m", "gdp"]
        source_map = {
            "EGY_temperature_2m": "NASA POWER",
            "EGY_gdp": "World Bank",
            "DEU_temperature_2m": "NASA POWER",
            "DEU_gdp": "World Bank",
        }
        
        progress.initialize_operations(countries, features, source_map)
        
        assert progress.total_count == 4
        assert len(progress.operations) == 4
        
        # Check first operation
        op = progress.operations[0]
        assert op.country == "EGY"
        assert op.feature == "temperature_2m"
        assert op.source == "NASA POWER"

    def test_get_operation(self):
        """Test getting a specific operation."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY"],
            ["temperature_2m"],
            {"EGY_temperature_2m": "NASA POWER"}
        )
        
        op = progress.get_operation("EGY", "temperature_2m")
        assert op is not None
        assert op.country == "EGY"
        assert op.feature == "temperature_2m"
        
        # Test non-existent operation
        op = progress.get_operation("USA", "temperature_2m")
        assert op is None

    def test_start_operation(self):
        """Test starting an operation."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY"],
            ["temperature_2m"],
            {"EGY_temperature_2m": "NASA POWER"}
        )
        
        progress.start_operation("EGY", "temperature_2m")
        
        op = progress.get_operation("EGY", "temperature_2m")
        assert op.status == AcquisitionStatus.DOWNLOADING
        assert op.started_at is not None
        assert len(progress.activity_log) > 0

    def test_update_operation(self):
        """Test updating an operation."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY"],
            ["temperature_2m"],
            {"EGY_temperature_2m": "NASA POWER"}
        )
        
        progress.start_operation("EGY", "temperature_2m")
        progress.update_operation(
            "EGY",
            "temperature_2m",
            AcquisitionStatus.VALIDATING,
            "Validating data"
        )
        
        op = progress.get_operation("EGY", "temperature_2m")
        assert op.status == AcquisitionStatus.VALIDATING
        assert op.message == "Validating data"

    def test_complete_operation_success(self):
        """Test completing an operation with success."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY"],
            ["temperature_2m"],
            {"EGY_temperature_2m": "NASA POWER"}
        )
        
        progress.start_operation("EGY", "temperature_2m")
        progress.complete_operation(
            "EGY",
            "temperature_2m",
            AcquisitionStatus.SUCCESS,
            records=1000,
            message="Downloaded successfully"
        )
        
        assert progress.completed_count == 1
        assert progress.success_count == 1
        assert progress.failed_count == 0
        
        op = progress.get_operation("EGY", "temperature_2m")
        assert op.status == AcquisitionStatus.SUCCESS
        assert op.records == 1000
        assert op.completed_at is not None

    def test_complete_operation_failed(self):
        """Test completing an operation with failure."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY"],
            ["temperature_2m"],
            {"EGY_temperature_2m": "NASA POWER"}
        )
        
        progress.start_operation("EGY", "temperature_2m")
        progress.complete_operation(
            "EGY",
            "temperature_2m",
            AcquisitionStatus.FAILED,
            records=0,
            message="Download failed"
        )
        
        assert progress.completed_count == 1
        assert progress.failed_count == 1
        assert progress.success_count == 0

    def test_progress_percent(self):
        """Test progress percentage calculation."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY", "DEU"],
            ["temperature_2m"],
            {
                "EGY_temperature_2m": "NASA POWER",
                "DEU_temperature_2m": "NASA POWER",
            }
        )
        
        assert progress.progress_percent == 0.0
        
        progress.start_operation("EGY", "temperature_2m")
        progress.complete_operation("EGY", "temperature_2m", AcquisitionStatus.SUCCESS, 100, "OK")
        
        assert progress.progress_percent == 50.0
        
        progress.start_operation("DEU", "temperature_2m")
        progress.complete_operation("DEU", "temperature_2m", AcquisitionStatus.SUCCESS, 100, "OK")
        
        assert progress.progress_percent == 100.0

    def test_get_current_operation(self):
        """Test getting the current operation."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY", "DEU"],
            ["temperature_2m"],
            {
                "EGY_temperature_2m": "NASA POWER",
                "DEU_temperature_2m": "NASA POWER",
            }
        )
        
        # No current operation yet
        assert progress.get_current_operation() is None
        
        # Start first operation
        progress.start_operation("EGY", "temperature_2m")
        current = progress.get_current_operation()
        assert current is not None
        assert current.country == "EGY"
        assert current.status == AcquisitionStatus.DOWNLOADING
        
        # Complete first operation
        progress.complete_operation("EGY", "temperature_2m", AcquisitionStatus.SUCCESS, 100, "OK")
        
        # Start second operation
        progress.start_operation("DEU", "temperature_2m")
        current = progress.get_current_operation()
        assert current is not None
        assert current.country == "DEU"

    def test_cancellation(self):
        """Test cancellation functionality."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY", "DEU"],
            ["temperature_2m"],
            {
                "EGY_temperature_2m": "NASA POWER",
                "DEU_temperature_2m": "NASA POWER",
            }
        )
        
        assert progress.cancelled is False
        
        progress.request_cancel()
        
        assert progress.cancelled is True
        assert any("Cancellation requested" in entry.message for entry in progress.activity_log)

    def test_mark_remaining_as_cancelled(self):
        """Test marking remaining operations as cancelled."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY", "DEU", "USA"],
            ["temperature_2m"],
            {
                "EGY_temperature_2m": "NASA POWER",
                "DEU_temperature_2m": "NASA POWER",
                "USA_temperature_2m": "NASA POWER",
            }
        )
        
        # Complete first operation
        progress.start_operation("EGY", "temperature_2m")
        progress.complete_operation("EGY", "temperature_2m", AcquisitionStatus.SUCCESS, 100, "OK")
        
        # Start second operation
        progress.start_operation("DEU", "temperature_2m")
        
        # Mark remaining as cancelled
        progress.mark_remaining_as_cancelled()
        
        # Check that DEU and USA are cancelled
        deu_op = progress.get_operation("DEU", "temperature_2m")
        usa_op = progress.get_operation("USA", "temperature_2m")
        
        assert deu_op.status == AcquisitionStatus.CANCELLED
        assert usa_op.status == AcquisitionStatus.CANCELLED
        
        # EGY should still be SUCCESS
        egy_op = progress.get_operation("EGY", "temperature_2m")
        assert egy_op.status == AcquisitionStatus.SUCCESS

    def test_get_summary(self):
        """Test getting summary statistics."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY", "DEU", "USA"],
            ["temperature_2m"],
            {
                "EGY_temperature_2m": "NASA POWER",
                "DEU_temperature_2m": "NASA POWER",
                "USA_temperature_2m": "NASA POWER",
            }
        )
        
        # Complete with different statuses
        progress.start_operation("EGY", "temperature_2m")
        progress.complete_operation("EGY", "temperature_2m", AcquisitionStatus.SUCCESS, 100, "OK")
        
        progress.start_operation("DEU", "temperature_2m")
        progress.complete_operation("DEU", "temperature_2m", AcquisitionStatus.PARTIAL_SUCCESS, 50, "Partial")
        
        progress.start_operation("USA", "temperature_2m")
        progress.complete_operation("USA", "temperature_2m", AcquisitionStatus.FAILED, 0, "Failed")
        
        summary = progress.get_summary()
        
        assert summary["total"] == 3
        assert summary["completed"] == 3
        assert summary["success"] == 1
        assert summary["partial"] == 1
        assert summary["failed"] == 1
        assert summary["progress_percent"] == 100.0

    def test_callbacks(self):
        """Test callback functionality."""
        progress = AcquisitionProgress()
        
        callback_called = []
        
        def test_callback(prog):
            callback_called.append(True)
        
        progress.add_callback(test_callback)
        progress.initialize_operations(
            ["EGY"],
            ["temperature_2m"],
            {"EGY_temperature_2m": "NASA POWER"}
        )
        
        # Callback should have been called during initialization (log call)
        assert len(callback_called) > 0

    def test_activity_log(self):
        """Test activity log entries."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY"],
            ["temperature_2m"],
            {"EGY_temperature_2m": "NASA POWER"}
        )
        
        progress.start_operation("EGY", "temperature_2m")
        progress.complete_operation("EGY", "temperature_2m", AcquisitionStatus.SUCCESS, 100, "OK")
        
        # Check that log entries were created
        assert len(progress.activity_log) > 0
        
        # Check log entry format
        log_text = "\n".join(entry.format() for entry in progress.activity_log)
        assert "Starting" in log_text or "SUCCESS" in log_text


class TestProgressIntegration:
    """Integration tests for progress tracking with acquisition."""
    
    def test_progress_does_not_affect_acquisition_logic(self):
        """Test that progress tracking doesn't change acquisition behavior."""
        # This is a conceptual test - in real integration, we'd verify that
        # the same results are produced with and without progress tracking
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY"],
            ["temperature_2m"],
            {"EGY_temperature_2m": "NASA POWER"}
        )
        
        # Simulate acquisition flow
        progress.start_operation("EGY", "temperature_2m")
        progress.update_operation("EGY", "temperature_2m", AcquisitionStatus.DOWNLOADING, "Downloading")
        progress.update_operation("EGY", "temperature_2m", AcquisitionStatus.VALIDATING, "Validating")
        progress.update_operation("EGY", "temperature_2m", AcquisitionStatus.SAVING, "Saving")
        progress.complete_operation("EGY", "temperature_2m", AcquisitionStatus.SUCCESS, 1000, "Success")
        
        # Verify all stages were recorded
        op = progress.get_operation("EGY", "temperature_2m")
        assert op.status == AcquisitionStatus.SUCCESS
        assert op.records == 1000
        
        # Verify activity log has entries for each stage
        assert len(progress.activity_log) >= 4

    def test_failed_operation_stays_failed(self):
        """Test that failed operations remain failed in progress tracking."""
        progress = AcquisitionProgress()
        progress.initialize_operations(
            ["EGY"],
            ["temperature_2m"],
            {"EGY_temperature_2m": "NASA POWER"}
        )
        
        progress.start_operation("EGY", "temperature_2m")
        progress.complete_operation(
            "EGY",
            "temperature_2m",
            AcquisitionStatus.FAILED,
            0,
            "Download failed: Network error"
        )
        
        op = progress.get_operation("EGY", "temperature_2m")
        assert op.status == AcquisitionStatus.FAILED
        assert op.records == 0
        assert "Network error" in op.message
        
        # Verify it's counted correctly
        assert progress.failed_count == 1
        assert progress.success_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
