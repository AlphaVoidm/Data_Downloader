"""Live acquisition progress tracking system.

Tracks acquisition progress without changing the acquisition logic.
Provides callbacks for the acquisition engine to report status updates.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any
from enum import Enum


class AcquisitionStatus(Enum):
    """Status for each acquisition operation."""
    WAITING = "WAITING"
    DOWNLOADING = "DOWNLOADING"
    VALIDATING = "VALIDATING"
    SAVING = "SAVING"
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    NO_DATA_AVAILABLE = "NO_DATA_AVAILABLE"
    SOURCE_NOT_COVERED = "SOURCE_NOT_COVERED"
    MAPPING_MISSING = "MAPPING_MISSING"
    CANCELLED = "CANCELLED"


@dataclass
class AcquisitionOperation:
    """Tracks a single country-feature-source acquisition."""
    country: str
    feature: str
    source: str
    status: AcquisitionStatus = AcquisitionStatus.WAITING
    records: int = 0
    duration_seconds: float = 0.0
    message: str = ""
    started_at: float | None = None
    completed_at: float | None = None
    
    def start(self):
        """Mark operation as started."""
        self.started_at = time.time()
        self.status = AcquisitionStatus.DOWNLOADING
    
    def update_status(self, status: AcquisitionStatus, message: str = "", records: int = 0):
        """Update operation status."""
        self.status = status
        if message:
            self.message = message
        if records:
            self.records = records
        if status in (AcquisitionStatus.SUCCESS, AcquisitionStatus.PARTIAL_SUCCESS, 
                     AcquisitionStatus.FAILED, AcquisitionStatus.CANCELLED):
            self.completed_at = time.time()
            if self.started_at:
                self.duration_seconds = self.completed_at - self.started_at
    
    def to_dict(self) -> dict:
        """Convert to dictionary for display."""
        return {
            "country": self.country,
            "feature": self.feature,
            "source": self.source,
            "status": self.status.value,
            "records": self.records,
            "duration": f"{self.duration_seconds:.1f}s",
            "message": self.message,
        }


@dataclass
class ActivityLogEntry:
    """Single entry in the activity log."""
    timestamp: datetime
    message: str
    
    def format(self) -> str:
        """Format for display."""
        time_str = self.timestamp.strftime("%H:%M:%S")
        return f"[{time_str}] {self.message}"


@dataclass
class AcquisitionProgress:
    """Main progress tracker for acquisition operations."""
    operations: list[AcquisitionOperation] = field(default_factory=list)
    activity_log: list[ActivityLogEntry] = field(default_factory=list)
    cancelled: bool = False
    _callbacks: list[Callable] = field(default_factory=list)
    
    def add_callback(self, callback: Callable):
        """Add a callback to be called on progress updates."""
        self._callbacks.append(callback)
    
    def _notify(self):
        """Notify all callbacks of progress update."""
        for callback in self._callbacks:
            try:
                callback(self)
            except Exception:
                pass  # Don't let callback errors break acquisition
    
    def initialize_operations(self, countries: list[str], features: list[str], 
                             source_map: dict[str, str]):
        """Initialize operations for all country-feature combinations."""
        self.operations = []
        for country in countries:
            for feature in features:
                source = source_map.get(f"{country}_{feature}", "unknown")
                op = AcquisitionOperation(
                    country=country,
                    feature=feature,
                    source=source
                )
                self.operations.append(op)
        self.log(f"Initialized {len(self.operations)} acquisition operations")
    
    def log(self, message: str):
        """Add entry to activity log."""
        entry = ActivityLogEntry(
            timestamp=datetime.now(),
            message=message
        )
        self.activity_log.append(entry)
        self._notify()
    
    def get_operation(self, country: str, feature: str) -> AcquisitionOperation | None:
        """Get operation for specific country-feature."""
        for op in self.operations:
            if op.country == country and op.feature == feature:
                return op
        return None
    
    def start_operation(self, country: str, feature: str):
        """Mark an operation as started."""
        op = self.get_operation(country, feature)
        if op:
            op.start()
            self.log(f"Starting: {country} → {feature} → {op.source}")
            self._notify()
    
    def update_operation(self, country: str, feature: str, 
                        status: AcquisitionStatus, message: str = "", 
                        records: int = 0):
        """Update operation status."""
        op = self.get_operation(country, feature)
        if op:
            op.update_status(status, message, records)
            
            # Log based on status
            if status == AcquisitionStatus.VALIDATING:
                self.log(f"Validating: {country} → {feature}")
            elif status == AcquisitionStatus.SAVING:
                self.log(f"Saving: {country} → {feature}")
            elif status == AcquisitionStatus.SUCCESS:
                self.log(f"✓ Success: {country} → {feature} ({records} records)")
            elif status == AcquisitionStatus.PARTIAL_SUCCESS:
                self.log(f"⚠ Partial: {country} → {feature} ({records} records) - {message}")
            elif status == AcquisitionStatus.FAILED:
                self.log(f"✗ Failed: {country} → {feature} - {message}")
            
            self._notify()
    
    def complete_operation(self, country: str, feature: str, status: AcquisitionStatus,
                          records: int, message: str):
        """Mark an operation as complete."""
        op = self.get_operation(country, feature)
        if op:
            op.update_status(status, message, records)
            
            # Log completion
            status_symbol = {
                AcquisitionStatus.SUCCESS: "✓",
                AcquisitionStatus.PARTIAL_SUCCESS: "⚠",
                AcquisitionStatus.FAILED: "✗",
                AcquisitionStatus.AUTH_REQUIRED: "🔑",
                AcquisitionStatus.NO_DATA_AVAILABLE: "⚪",
                AcquisitionStatus.SOURCE_NOT_COVERED: "⚪",
            }.get(status, "○")
            
            self.log(f"{status_symbol} {country} → {feature}: {status.value} "
                    f"({records} records, {op.duration_seconds:.1f}s)")
            self._notify()
    
    def request_cancel(self):
        """Request cancellation of acquisition."""
        self.cancelled = True
        self.log("⚠ Cancellation requested")
        self._notify()
    
    def mark_remaining_as_cancelled(self):
        """Mark all non-completed operations as cancelled."""
        for op in self.operations:
            if op.status not in (AcquisitionStatus.SUCCESS, 
                                AcquisitionStatus.PARTIAL_SUCCESS,
                                AcquisitionStatus.FAILED):
                op.update_status(AcquisitionStatus.CANCELLED, "Cancelled by user")
        self.log("Remaining operations cancelled")
        self._notify()
    
    @property
    def completed_count(self) -> int:
        """Count of completed operations."""
        return sum(1 for op in self.operations 
                  if op.status in (AcquisitionStatus.SUCCESS,
                                  AcquisitionStatus.PARTIAL_SUCCESS,
                                  AcquisitionStatus.FAILED,
                                  AcquisitionStatus.AUTH_REQUIRED,
                                  AcquisitionStatus.NO_DATA_AVAILABLE,
                                  AcquisitionStatus.SOURCE_NOT_COVERED,
                                  AcquisitionStatus.CANCELLED))
    
    @property
    def total_count(self) -> int:
        """Total number of operations."""
        return len(self.operations)
    
    @property
    def success_count(self) -> int:
        """Count of successful operations."""
        return sum(1 for op in self.operations 
                  if op.status == AcquisitionStatus.SUCCESS)
    
    @property
    def partial_count(self) -> int:
        """Count of partial success operations."""
        return sum(1 for op in self.operations 
                  if op.status == AcquisitionStatus.PARTIAL_SUCCESS)
    
    @property
    def failed_count(self) -> int:
        """Count of failed operations."""
        return sum(1 for op in self.operations 
                  if op.status == AcquisitionStatus.FAILED)
    
    @property
    def auth_required_count(self) -> int:
        """Count of auth required operations."""
        return sum(1 for op in self.operations 
                  if op.status == AcquisitionStatus.AUTH_REQUIRED)
    
    @property
    def no_data_count(self) -> int:
        """Count of no data operations."""
        return sum(1 for op in self.operations 
                  if op.status == AcquisitionStatus.NO_DATA_AVAILABLE)
    
    @property
    def not_covered_count(self) -> int:
        """Count of not covered operations."""
        return sum(1 for op in self.operations 
                  if op.status == AcquisitionStatus.SOURCE_NOT_COVERED)
    
    @property
    def progress_percent(self) -> float:
        """Progress as percentage."""
        if self.total_count == 0:
            return 0.0
        return (self.completed_count / self.total_count) * 100
    
    def get_current_operation(self) -> AcquisitionOperation | None:
        """Get the currently running operation."""
        for op in self.operations:
            if op.status in (AcquisitionStatus.DOWNLOADING, 
                           AcquisitionStatus.VALIDATING,
                           AcquisitionStatus.SAVING):
                return op
        return None
    
    def get_summary(self) -> dict:
        """Get summary statistics."""
        return {
            "total": self.total_count,
            "completed": self.completed_count,
            "success": self.success_count,
            "partial": self.partial_count,
            "failed": self.failed_count,
            "auth_required": self.auth_required_count,
            "no_data": self.no_data_count,
            "not_covered": self.not_covered_count,
            "progress_percent": self.progress_percent,
        }


__all__ = [
    "AcquisitionStatus",
    "AcquisitionOperation",
    "ActivityLogEntry",
    "AcquisitionProgress",
]
