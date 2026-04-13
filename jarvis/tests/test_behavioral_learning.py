"""
tests/test_behavioral_learning.py
─────────────────────────────────
Integration tests covering event clustering, extraction, and confidence scoring
within the Behavioral Modeling subsystem.
"""

import os
import json
import pytest
from datetime import datetime

from learning.storage import JSONEventStore, JSONWorkflowStore
from learning.pattern_extractor import PatternExtractor

@pytest.fixture
def mock_stores(tmp_path):
    import learning.storage as storage
    import learning.pattern_extractor as extractor_module
    
    old_event_store = storage.event_store
    old_workflow_store = storage.workflow_store
    
    events_path = str(tmp_path / "events.json")
    workflows_path = str(tmp_path / "workflows.json")
    
    temp_event_store = JSONEventStore(events_path)
    temp_workflow_store = JSONWorkflowStore(workflows_path)
    
    storage.event_store = temp_event_store
    storage.workflow_store = temp_workflow_store
    
    extractor_module.event_store = temp_event_store
    extractor_module.workflow_store = temp_workflow_store
    
    yield temp_event_store, temp_workflow_store
    
    storage.event_store = old_event_store
    storage.workflow_store = old_workflow_store
    extractor_module.event_store = old_event_store
    extractor_module.workflow_store = old_workflow_store

def test_pattern_extraction_clustering(mock_stores):
    event_store, workflow_store = mock_stores
    
    # ── Simulate 3 identical successful tasks ───────────────────
    for i in range(3):
        t_id_1 = f"task_A_{i}"
        event_store.save_event({"event_type": "task_start", "task_id": t_id_1, "intent": "Build website", "timestamp": "2026-04-13T00:00:00Z"})
        event_store.save_event({"event_type": "tool_usage", "task_id": t_id_1, "tool": "chatgpt", "action": "Ask LLM", "timestamp": "2026-04-13T00:00:01Z"})
        event_store.save_event({"event_type": "tool_usage", "task_id": t_id_1, "tool": "create_file", "action": "Save HTML", "timestamp": "2026-04-13T00:00:02Z"})
        event_store.save_event({"event_type": "task_end", "task_id": t_id_1, "success": True, "timestamp": "2026-04-13T00:00:03Z"})
        
    # ── Simulate 1 distinct task that should not cluster ────────
    t_id_2 = "task_B_0"
    event_store.save_event({"event_type": "task_start", "task_id": t_id_2, "intent": "Analyze Data", "timestamp": "2026-04-13T00:00:00Z"})
    event_store.save_event({"event_type": "tool_usage", "task_id": t_id_2, "tool": "execute_python", "action": "Run script", "timestamp": "2026-04-13T00:00:01Z"})
    event_store.save_event({"event_type": "task_end", "task_id": t_id_2, "success": True, "timestamp": "2026-04-13T00:00:02Z"})
    
    # Extract
    extractor = PatternExtractor()
    workflows_generated = extractor.extract_and_store()
    
    assert workflows_generated == 1
    
    workflows = workflow_store.load_all()
    assert len(workflows) == 1, "Only one workflow should have sufficient frequency to be learned."
    
    w1 = workflows[0]
    # associated_intents now stores canonical slugs (e.g. "website_building"),
    # not raw user strings, because the intent normalizer runs during extraction.
    assert "website_building" in w1["associated_intents"]
    assert w1["frequency"] == 3
    assert w1["success_rate"] == 1.0
    assert len(w1["steps"]) == 2
    assert w1["steps"][0]["tool"] == "chatgpt"

def test_pattern_extraction_handles_failure(mock_stores):
    event_store, workflow_store = mock_stores
    
    # Simulate 3 attempts, but only 1 succeeds. Success rate = 0.33 (< 0.5 threshold)
    for i in range(3):
        t_id = f"task_{i}"
        event_store.save_event({"event_type": "task_start", "task_id": t_id, "intent": "Deploy server", "timestamp": "2026-04-13T00:00:00Z"})
        event_store.save_event({"event_type": "tool_usage", "task_id": t_id, "tool": "run_command", "action": "Deploy", "timestamp": "2026-04-13T00:00:00Z"})
        event_store.save_event({"event_type": "task_end", "task_id": t_id, "success": (i == 0), "timestamp": "2026-04-13T00:00:00Z"}) # Only first succeeds

    extractor = PatternExtractor()
    extractor.extract_and_store()
    
    workflows = workflow_store.load_all()
    assert len(workflows) == 0, "Low success rate workflows should be ignored."
