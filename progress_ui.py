"""Streamlit UI components for live acquisition progress display.

Provides rendering functions for:
- Overall progress bar
- Per-operation status table
- Activity log
- Final summary
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import streamlit as st
import pandas as pd

from progress_tracker import (
    AcquisitionProgress,
    AcquisitionStatus,
    AcquisitionOperation,
)


def render_progress_header(progress: AcquisitionProgress, start_time: float):
    """Render the overall progress header with progress bar."""
    st.markdown("### 📡 ACQUISITION IN PROGRESS")
    
    # Calculate elapsed time
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    hours, minutes = divmod(minutes, 60)
    elapsed_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    # Progress bar
    completed = progress.completed_count
    total = progress.total_count
    percent = progress.progress_percent / 100.0 if total > 0 else 0
    
    st.progress(percent)
    st.markdown(f"**Progress:** {completed} / {total} ({progress.progress_percent:.1f}%)")
    
    # Current operation
    current = progress.get_current_operation()
    if current:
        st.markdown(f"**Current:** {current.country} → {current.feature} → {current.source}")
        status_text = {
            AcquisitionStatus.DOWNLOADING: "Downloading...",
            AcquisitionStatus.VALIDATING: "Validating...",
            AcquisitionStatus.SAVING: "Saving...",
        }.get(current.status, "Processing...")
        st.markdown(f"**Status:** {status_text}")
    
    # Elapsed time
    st.markdown(f"**Elapsed:** {elapsed_str}")
    
    st.divider()


def render_operation_table(progress: AcquisitionProgress):
    """Render the per-operation status table."""
    st.markdown("#### Operations")
    
    # Status symbols
    status_symbols = {
        AcquisitionStatus.WAITING: "○",
        AcquisitionStatus.DOWNLOADING: "⟳",
        AcquisitionStatus.VALIDATING: "⟳",
        AcquisitionStatus.SAVING: "⟳",
        AcquisitionStatus.SUCCESS: "✓",
        AcquisitionStatus.PARTIAL_SUCCESS: "⚠",
        AcquisitionStatus.FAILED: "✗",
        AcquisitionStatus.AUTH_REQUIRED: "🔑",
        AcquisitionStatus.NO_DATA_AVAILABLE: "⚪",
        AcquisitionStatus.SOURCE_NOT_COVERED: "⚪",
        AcquisitionStatus.MAPPING_MISSING: "⚠",
        AcquisitionStatus.CANCELLED: "⊘",
    }
    
    # Build table data
    rows = []
    for op in progress.operations:
        symbol = status_symbols.get(op.status, "○")
        rows.append({
            "Status": f"{symbol} {op.status.value}",
            "Country": op.country,
            "Feature": op.feature,
            "Source": op.source,
            "Records": op.records if op.records > 0 else "-",
            "Duration": f"{op.duration_seconds:.1f}s" if op.duration_seconds > 0 else "-",
            "Message": op.message[:50] if op.message else "-",
        })
    
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_activity_log(progress: AcquisitionProgress, max_entries: int = 20):
    """Render the activity log."""
    st.markdown("#### Activity Log")
    
    # Show last N entries
    entries = progress.activity_log[-max_entries:]
    
    # Format entries
    log_text = "\n".join(entry.format() for entry in entries)
    
    # Use a scrollable container
    st.code(log_text, language=None)


def render_final_summary(progress: AcquisitionProgress, start_time: float):
    """Render the final acquisition summary."""
    st.markdown("### ✅ ACQUISITION COMPLETE")
    
    # Calculate total elapsed time
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    hours, minutes = divmod(minutes, 60)
    elapsed_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Operations", progress.total_count)
    with col2:
        st.metric("✓ Success", progress.success_count)
    with col3:
        st.metric("⚠ Partial", progress.partial_count)
    with col4:
        st.metric("✗ Failed", progress.failed_count)
    
    col5, col6, col7 = st.columns(3)
    with col5:
        st.metric("🔑 Auth Required", progress.auth_required_count)
    with col6:
        st.metric("⚪ No Data", progress.no_data_count)
    with col7:
        st.metric("⚪ Not Covered", progress.not_covered_count)
    
    st.markdown(f"**Total Elapsed Time:** {elapsed_str}")
    
    st.divider()
    
    # Detailed operation table
    st.markdown("#### Final Operations Report")
    render_operation_table(progress)
    
    # Full activity log
    with st.expander("📋 Full Activity Log", expanded=False):
        render_activity_log(progress, max_entries=1000)


def render_cancel_button(progress: AcquisitionProgress):
    """Render the cancel acquisition button."""
    if st.button("⊘ Cancel Acquisition", type="secondary"):
        progress.request_cancel()
        st.warning("Cancellation requested. Finishing current operation...")
        return True
    return False


def render_progress_ui(progress: AcquisitionProgress, start_time: float, 
                      show_cancel: bool = True):
    """Render the complete progress UI."""
    # Check if acquisition is complete
    is_complete = progress.completed_count == progress.total_count
    
    if is_complete:
        render_final_summary(progress, start_time)
    else:
        render_progress_header(progress, start_time)
        
        if show_cancel:
            render_cancel_button(progress)
        
        render_operation_table(progress)
        
        st.divider()
        
        render_activity_log(progress)


def init_progress_display():
    """Initialize session state for progress display."""
    if "acquisition_progress" not in st.session_state:
        st.session_state.acquisition_progress = AcquisitionProgress()
    
    if "acquisition_start_time" not in st.session_state:
        st.session_state.acquisition_start_time = time.time()
    
    if "acquisition_complete" not in st.session_state:
        st.session_state.acquisition_complete = False


def get_progress_tracker() -> AcquisitionProgress:
    """Get the current progress tracker from session state."""
    return st.session_state.acquisition_progress


def reset_progress_display():
    """Reset the progress display for a new acquisition."""
    st.session_state.acquisition_progress = AcquisitionProgress()
    st.session_state.acquisition_start_time = time.time()
    st.session_state.acquisition_complete = False


__all__ = [
    "render_progress_ui",
    "render_progress_header",
    "render_operation_table",
    "render_activity_log",
    "render_final_summary",
    "render_cancel_button",
    "init_progress_display",
    "get_progress_tracker",
    "reset_progress_display",
]
