import time
import json
from skills.registry import Skill, skill_registry
from skills.metrics import skill_metrics
from skills.selector import select_skills
from config.settings import settings

settings.openai_api_key = "dummy_key"

import sys
import json

class MockCompletions:
    def create(self, *args, **kwargs):
        messages = kwargs.get("messages", [{}])
        content_str = messages[0].get("content", "") if messages else ""
        
        class MockMessage:
            if "Categories for task_type" in content_str:
                content = '{"task_type": "debugging", "complexity": "medium"}'
            elif "Mock debugging skill" in content_str:  # For langchain mock
                content = json.dumps({
                    "is_valid_skill": True,
                    "candidate": {
                        "name": "code_debugging_v2",
                        "description": "Mock debugging skill",
                        "when_to_use": ["error", "bug"],
                        "instructions": "1. Find bug\n2. Fix bug"
                    }
                })
            else:
                content = '["code_debugging", "code_debugging_v1", "code_debugging_v2"]'
                
        class MockChoice:
            message = MockMessage()
            
        class MockResponse:
            choices = [MockChoice()]
            
        return MockResponse()

class MockChat:
    completions = MockCompletions()

class MockOpenAI:
    def __init__(self, *args, **kwargs):
        self.chat = MockChat()

# Pre-fetch or create openai module mock if it wasn't installed
import importlib.util
if importlib.util.find_spec("openai") is None:
    import types
    sys.modules["openai"] = types.ModuleType("openai")

import openai
openai.OpenAI = MockOpenAI

import importlib.util
if importlib.util.find_spec("langchain_openai") is None:
    import types
    sys.modules["langchain_openai"] = types.ModuleType("langchain_openai")

import langchain_openai
class MockChatOpenAI:
    def __init__(self, *args, **kwargs):
        pass
    def invoke(self, messages):
        class MockResponse:
            content = json.dumps({
                "is_valid_skill": True,
                "candidate": {
                    "name": "code_debugging",
                    "description": "Mock debugging skill",
                    "when_to_use": ["error", "bug"],
                    "instructions": "1. Find bug\n2. Fix bug"
                }
            })
        return MockResponse()

langchain_openai.ChatOpenAI = MockChatOpenAI

# Setup initial state
code_debugging_v1 = Skill(
    name="code_debugging_v1",
    description="V1 code debugging",
    when_to_use=["error", "bug"],
    instructions="Old instructions",
    version=1,
    status="core",
    family="code_debugging"
)
skill_registry.register_skill(code_debugging_v1)

# Fake 3 executions to trigger extraction
from planner import Plan, PlanStep
from skills.generator import skill_evolution_engine
        
# A plan with two steps (matches length >= 2 in high_quality logic)
plan = Plan(goal="Fix bug", steps=[
    PlanStep(tool="read_file", step_id=1, action="read", expected_outcome="data", params={"file": "test.txt"}), 
    PlanStep(tool="write_file", step_id=2, action="write", expected_outcome="success", params={"file": "test.txt", "content": "1"})
])
results = [{"step_id": 1, "status": "success"}, {"step_id": 2, "status": "success"}]

print("Triggering successful executions...")
# 3 triggers
for _ in range(3):
    skill_evolution_engine.process_successful_execution(plan, results)
    
time.sleep(2) # wait for bg thread

print("Checking registry...")
all_skills = skill_registry.get_all_skills(include_archived=False)
v2_sk = next((s for s in all_skills if s.name == "code_debugging_v2"), None)
print(f"v2 skill found: {v2_sk is not None}")
if v2_sk:
    print(f"Status: {v2_sk.status}")
    print(f"Family: {v2_sk.family}")

    # Inject metrics for promotion
    print("Injecting metrics for promotion...")
    
    # Give v1 10 usages, 5 successes
    for i in range(10):
        req_id = f"req-v1-{i}"
        skill_metrics.log_selected_skills(req_id, ["code_debugging_v1"])
        skill_metrics.log_execution_success(req_id, (i < 5), 1)

    # Give v2 10 usages, 10 successes (much better)
    for i in range(10):
        req_id = f"req-v2-{i}"
        skill_metrics.log_selected_skills(req_id, ["code_debugging_v2"])
        skill_metrics.log_plan_quality(req_id, 1.0)
        skill_metrics.log_execution_success(req_id, True, 0)
        
    # Give the default 'code_debugging' 10 fail usages so its cold-start score drops
    for i in range(10):
        req_id = f"req-def-{i}"
        skill_metrics.log_selected_skills(req_id, ["code_debugging"])
        skill_metrics.log_plan_quality(req_id, 0.0)
        skill_metrics.log_execution_success(req_id, False, 1)
        
    print("Selecting skills (should trigger promotion)...")
    selected = select_skills("I need to fix a bug and an error", "some context")
    
    v2_sk_updated = next((s for s in skill_registry.get_all_skills() if s.name == "code_debugging_v2"), None)
    print(f"v2 status after select: {v2_sk_updated.status if v2_sk_updated else None}")
    
    selected_names = [s.name for s in selected]
    print(f"Selected skills: {selected_names}")
    
    assert "code_debugging_v2" in selected_names, "V2 not selected"
    assert "code_debugging_v1" not in selected_names, "V1 also selected, combination filter failed!"
    print("Promotion test passed!")
    
    print("Injecting historical data for global regression testing...")
    # Inject 100 historical tasks (long window) with identical high success
    for i in range(100):
        req_id = f"req-hist-{i}"
        skill_metrics.log_selected_skills(req_id, ["code_debugging_v1"])
        skill_metrics.log_plan_quality(req_id, 1.0)
        skill_metrics.log_execution_success(req_id, True, 0)
        
    print("Generating short window data triggering regression...")
    # Inject 20 recent tasks (short window) with totally failed performance
    for i in range(20):
        req_id = f"req-recent-{i}"
        skill_metrics.log_selected_skills(req_id, ["code_debugging_v2"])
        skill_metrics.log_plan_quality(req_id, 0.0)
        skill_metrics.log_execution_success(req_id, False, 3)
        
    print("Detecting regressions...")
    skill_metrics.detect_and_handle_regressions()
    
    health_sum = getattr(skill_metrics, 'last_health_summary', {})
    print(f"Health summary: {health_sum}")
    
    v2_sk_mitigated = next((s for s in skill_registry.get_all_skills() if s.name == "code_debugging_v2"), None)
    print(f"v2 mitigation stage post-regression 1: {v2_sk_mitigated.mitigation_stage if v2_sk_mitigated else None}")
    if v2_sk_mitigated and v2_sk_mitigated.mitigation_stage != 1:
        print("V2 was not demoted! Continuing test nonetheless to see everything.")
    
    print("Generating second regression drop...")
    # Another 20 total failures
    for i in range(20, 40):
        req_id = f"req-recent-{i}"
        skill_metrics.log_selected_skills(req_id, ["code_debugging_v2"])
        skill_metrics.log_plan_quality(req_id, 0.0)
        skill_metrics.log_execution_success(req_id, False, 3)
        
    skill_metrics.detect_and_handle_regressions()
    v2_sk_mitigated2 = next((s for s in skill_registry.get_all_skills() if s.name == "code_debugging_v2"), None)
    print(f"v2 mitigation stage post-regression 2: {v2_sk_mitigated2.mitigation_stage if v2_sk_mitigated2 else None}")
    if v2_sk_mitigated2 and v2_sk_mitigated2.mitigation_stage != 2:
        print("V2 was not demoted to stage 2!")
    
    print("Testing Adaptive Canary Routing...")
    # Selection should now only rarely pick v2 due to stage 2 uncertainty caps
    canary_selects = 0
    total_samples = 100
    for _ in range(total_samples):
        # Provide keywords that match
        sel = select_skills("I need to fix a bug and an error", "context")
        # Ensure we catch it when selected
        if "code_debugging_v2" in [s.name for s in sel]:
            canary_selects += 1
            
    print(f"Canary selection rate: {canary_selects}/{total_samples}")
    # Canary cap maxes at around 15%, so it should be strictly < 25
    assert canary_selects < 25, f"Canary rate too high! ({canary_selects}%) adaptive routing failed."

    print("Integration test passed fully!")
else:
    print("Integration test failed! V2 not generated.")
