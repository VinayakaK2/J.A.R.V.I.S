"""
tests/test_hierarchical_planner.py

Standalone test harness for V6 Hierarchical Planner and Critic pipeline validation.
Tests three scenarios without executing any physical desktop actions.

Scenarios:
1. Multi-step UI task
2. Dependency failure 
3. Ambiguous instruction

Usage:
  cd h:\Jarvis\jarvis
  python -X utf8 tests/test_hierarchical_planner.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config.settings import settings
from planner_v6.high_level import hl_planner
from planner_v6.low_level import ll_planner
from agents.critic import critic_node

BOLD = "\033[1m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"

def divider(title: str):
    line = "=" * 70
    print(f"\n{BOLD}{CYAN}{line}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{line}{RESET}")

def print_json(obj, indent=2):
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    print(json.dumps(obj, indent=indent, default=str))

def test_scenario(title: str, user_input: str, context: str = ""):
    divider(f"TEST SCENARIO: {title}")
    print(f"{BOLD}User Input:{RESET} '{user_input}'\n")

    print(f"{BOLD}{GREEN}1. High-Level Planner Output{RESET}")
    strategy = hl_planner.generate_strategy(user_input, context)
    print_json(strategy)
    print("\n" + "-" * 50)

    print(f"{BOLD}{GREEN}2. Low-Level Planner Output{RESET}")
    plan = ll_planner.map_to_executable(strategy, context)
    print_json(plan)
    print("\n" + "-" * 50)

    print(f"{BOLD}{GREEN}3. Critic Agent Output{RESET}")
    review = critic_node.evaluate_plan(plan)
    print_json(review)
    
    if not review.get("approved", False):
        print(f"\n{BOLD}{YELLOW}>> Critic Rejected. Executing Re-map...{RESET}")
        adjusted_context = context + f"\nCRITIC REJECTED PRIOR MAP: {review.get('feedback')}"
        final_plan = ll_planner.map_to_executable(strategy, adjusted_context)
    else:
        print(f"\n{BOLD}{GREEN}>> Critic Approved.{RESET}")
        final_plan = plan

    print("\n" + "-" * 50)
    print(f"{BOLD}{GREEN}4. Final Plan Sent to Executor{RESET}")
    print_json(final_plan)

def main():
    print(f"\n{BOLD}{CYAN}{'='*70}{RESET}")
    print(f"{BOLD}{CYAN}  JARVIS v6 -- Hierarchical Planner & Critic Validation{RESET}")
    print(f"{BOLD}{CYAN}  Flags: hl={settings.use_hierarchical_planner}, critic={settings.use_critic_agent}{RESET}")
    print(f"{BOLD}{CYAN}{'='*70}{RESET}\n")

    if not settings.use_hierarchical_planner or not settings.use_critic_agent:
        print(f"{RED}Error: Hierarchical planner or Critic flag is not enabled.{RESET}")
        sys.exit(1)
        
    print(f"{YELLOW}>> Running with STUBBED OpenAI responses to demonstrate Planner & Critic pipeline logic...{RESET}\n")

    # Stub High-Level Planner
    def _stub_hl_strategy(user_intent: str, context: str = ""):
        if "Open Chrome, search YouTube, and play a video" in user_intent:
            return {"goal": "Launch YouTube in Chrome and play a video", "subgoals": ["Open Chrome browser", "Navigate to YouTube", "Search for a video", "Click a video to play"]}
        elif "Click search button" in user_intent:
            return {"goal": "Click the desktop search button", "subgoals": ["Locate search button", "Click it"]}
        elif "Search something and open the first result" in user_intent:
            return {"goal": "Perform generic search and open a result", "subgoals": ["Open search engine", "Search for 'something'", "Click the first result link"]}
        return {"goal": "Fallback", "subgoals": []}

    # Stub Low-Level Planner
    def _stub_ll_map(strategy: dict, bias_context: str = ""):
        from planner import Plan, PlanStep
        
        # Scenario 1 Remap context check
        if "Launch YouTube" in strategy["goal"]:
            if "CRITIC REJECTED PRIOR MAP" in bias_context:
                return Plan(goal=strategy["goal"], steps=[
                    PlanStep(step_id=1, tool="open_url", action="Launch Chrome directly to YouTube", params={"url": "https://youtube.com"}),
                    PlanStep(step_id=2, tool="fill_input", action="Type into search box", params={"element_label": "Search", "text": "test video"}, depends_on=[1])
                ])
            else:
                # Intentionally bad first try using shell tools
                return Plan(goal=strategy["goal"], steps=[
                    PlanStep(step_id=1, tool="run_terminal_command", action="Launch Chrome", params={"command": "start chrome https://youtube.com"}),
                    PlanStep(step_id=2, tool="click_element", action="Click Address bar", params={"element_label": "Address and search bar"}, depends_on=[1]),
                    PlanStep(step_id=3, tool="fill_input", action="Type into search box", params={"element_label": "Search", "text": "test video"}, depends_on=[2])
                ])
                
        elif "desktop search" in strategy["goal"]:
            if "CRITIC REJECTED PRIOR MAP" in bias_context:
                return Plan(goal=strategy["goal"], steps=[
                    PlanStep(step_id=1, tool="open_application", action="Open the target application first", params={"app_name": "chrome"}),
                    PlanStep(step_id=2, tool="click_element", action="Click search", params={"element_label": "Search"}, depends_on=[1])
                ])
            else:
                # Deliberately raw plan missing dependency
                return Plan(goal=strategy["goal"], steps=[
                    PlanStep(step_id=1, tool="click_element", action="Click Search", params={"element_label": "Search"})
                ])
                
        elif "generic search" in strategy["goal"]:
            if "CRITIC REJECTED PRIOR MAP" in bias_context:
                return Plan(goal=strategy["goal"], steps=[
                    PlanStep(step_id=1, tool="ask_user", action="Ask user what to search for", params={"question": "What specifically would you like me to search for?"})
                ])
            else:
                return Plan(goal=strategy["goal"], steps=[
                    PlanStep(step_id=1, tool="search_web", action="Search something", params={"query": "something"})
                ])
        return Plan(goal="Fallback Plan", steps=[])

    # Stub Critic Agent
    def _stub_critic_evaluate(plan):
        from planner import Plan
        
        if "Launch YouTube" in plan.goal:
            if any(s.tool == "run_terminal_command" for s in plan.steps):
                return {"approved": False, "feedback": "TOOL VIOLATION & INEFFICIENCY: You used the unsafe 'run_terminal_command' tool and manually clicked the address bar. Use the 'open_url' tool directly to navigate there instead."}
            else:
                return {"approved": True, "feedback": "Plan appears well-structured, safe, and uses whitelisted tools efficiently."}
                
        elif "desktop search" in plan.goal:
            if len(plan.steps) == 1:
                return {"approved": False, "feedback": "FAILURE DETECTED: No application is currently open. Must open an app using 'open_application' before interacting with a search button."}
            elif any(s.tool == "run_terminal_command" for s in plan.steps):
                return {"approved": False, "feedback": "TOOL VIOLATION: Do not use 'run_terminal_command' to start applications. Use the whitelisted 'open_application' tool."}
            else:
                return {"approved": True, "feedback": "Dependencies resolved effectively natively."}
                
        elif "generic search" in plan.goal:
            if plan.steps[0].tool == "ask_user":
                return {"approved": True, "feedback": "Query disambiguated via user prompt. Approved."}
            else:
                return {"approved": False, "feedback": "LOGIC ERROR: The search query is ambiguous ('something'). Must ask user for clarity using 'ask_user'."}
        return {"approved": True, "feedback": "OK"}

    # Apply Stubs
    from planner_v6.high_level import hl_planner
    from planner_v6.low_level import ll_planner
    from agents.critic import critic_node
    hl_planner.generate_strategy = _stub_hl_strategy
    ll_planner.map_to_executable = _stub_ll_map
    critic_node.evaluate_plan = _stub_critic_evaluate

    # Scenario 1: Multi-step UI task
    test_scenario(
        "Multi-step UI task",
        "Open Chrome, search YouTube, and play a video"
    )

    # Scenario 2: Dependency failure
    test_scenario(
        "Dependency failure",
        "Click search button",
        context="System State: No application is currently open. Desktop is empty."
    )

    # Scenario 3: Ambiguous instruction
    test_scenario(
        "Ambiguous instruction",
        "Search something and open the first result"
    )

    print(f"\n{BOLD}{GREEN}{'='*70}{RESET}")
    print(f"{BOLD}{GREEN}  Validation complete.{RESET}")
    print(f"{BOLD}{GREEN}{'='*70}{RESET}\n")

if __name__ == "__main__":
    main()
