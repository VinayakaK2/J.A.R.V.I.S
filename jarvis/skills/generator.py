import json
import logging
import threading
import time
from typing import List, Dict, Any, Optional
from collections import defaultdict

from config.settings import settings
from skills.registry import Skill, skill_registry
from planner import Plan

logger = logging.getLogger(__name__)

class SkillEvolutionEngine:
    def __init__(self):
        # In-memory sequence counter. Key: tuple of tools, Value: count
        self._successful_sequences: Dict[tuple, int] = defaultdict(int)
        
        # Track active generation to avoid redundant identical sequence triggers
        self._active_generations = set()
        
    def process_successful_execution(self, plan: Plan, results: List[Dict]):
        """
        Background entry point. Hooked from executor.py.
        Analyzes a successful plan and spawns a thread to generate a skill if criteria are met.
        """
        if not settings.openai_api_key or settings.openai_api_key.startswith("your_"):
            return  # Needs LLM to extract
            
        tool_sequence = tuple([step.tool for step in plan.steps])
        self._successful_sequences[tool_sequence] += 1
        
        # Determine extraction condition
        seq_count = self._successful_sequences[tool_sequence]
        is_high_quality = len(tool_sequence) >= 2 and all(r.get("status") == "success" for r in results)
        
        if seq_count >= 3 or (is_high_quality and "error" not in str(results)):
            if tool_sequence not in self._active_generations:
                # Spawn fault-tolerant background thread
                self._active_generations.add(tool_sequence)
                t = threading.Thread(
                    target=self._run_extraction_pipeline_safe,
                    args=(plan, results, tool_sequence)
                )
                t.daemon = True
                t.start()
                
    def _run_extraction_pipeline_safe(self, plan: Plan, results: List[Dict], tool_sequence: tuple):
        """Fault-tolerant wrapper around the extraction pipeline."""
        max_retries = 2
        try:
            for attempt in range(max_retries):
                try:
                    self._run_extraction_pipeline(plan, results)
                    break  # Success
                except Exception as e:
                    logger.error(f"[SkillEvolution] Pipeline failed attempt {attempt+1}/{max_retries}: {e}")
                    time.sleep(2)
        finally:
            self._active_generations.discard(tool_sequence)

    def _run_extraction_pipeline(self, plan: Plan, results: List[Dict]):
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
        
        logger.info(f"[SkillEvolution] Extracting skill candidate for goal: {plan.goal}")
        
        # Clean results for prompt
        clean_results = []
        for r in results:
            clean_results.append({
                "tool": next((s.tool for s in plan.steps if s.step_id == r["step_id"]), "unknown"),
                "status": r["status"]
            })

        system_prompt = """You are the JARVIS Skill Abstraction Engine.
Your task is to analyze a successful execution trace and output a generalized, reusable 'Skill'.

STRICT ABSTRACTION RULES:
1. Ensure the generated skill represents a GENERALIZED pattern, not an instance-specific workflow.
2. REJECT CANDIDATES THAT ARE TOO SPECIFIC (e.g., exact URLs, specific UI elements, hardcoded texts).
3. If the plan was just a simple generic query (like "what is the weather"), output nothing.
4. Output in strictly valid JSON format matching this schema:
{
  "is_valid_skill": boolean,
  "reason_if_invalid": string,
  "candidate": {
    "name": "a_snake_case_name",
    "description": "General description of the skill",
    "when_to_use": ["keyword1", "keyword2"],
    "instructions": "Numbered list of reasoning instructions for the planner to follow"
  }
}"""
        
        user_prompt = f"Goal: {plan.goal}\nPlan Steps: {[s.dict(include={'tool', 'action'}) for s in plan.steps]}\nResults: {clean_results}"
        
        llm = ChatOpenAI(
            api_key=settings.openai_api_key,
            model="gpt-4o",
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        data = json.loads(response.content)
        
        if not data.get("is_valid_skill"):
            logger.info(f"[SkillEvolution] Rejected specific/invalid candidate: {data.get('reason_if_invalid')}")
            return
            
        candidate = data.get("candidate")
        self._validate_and_register(candidate)
        
    def _validate_and_register(self, candidate_dict: dict):
        # Semantic Novelty / Family Matching
        # We roughly match family by name or looking through existing skills' 'when_to_use'
        
        all_families = set(s.family for s in skill_registry.get_all_skills(include_archived=True) if s.family)
        
        # Simple heuristic: if the name is close to an existing family
        matched_family = None
        cand_name = candidate_dict["name"]
        
        for fam in all_families:
            if fam in cand_name or cand_name in fam:
                matched_family = fam
                break
                
        if not matched_family:
            # Check overlap in when_to_use
            cand_keywords = set(candidate_dict.get("when_to_use", []))
            best_overlap = 0
            for skill in skill_registry.get_all_skills():
                overlap = len(cand_keywords.intersection(set(skill.when_to_use)))
                if overlap > best_overlap and overlap >= 2:
                    best_overlap = overlap
                    matched_family = skill.family

        if matched_family:
            family_skills = [s for s in skill_registry.get_all_skills(include_archived=True) if s.family == matched_family]
            new_version = max([s.version for s in family_skills]) + 1
            final_name = f"{matched_family}_v{new_version}"
            final_family = matched_family
        else:
            final_name = cand_name
            final_family = cand_name
            new_version = 1
            
        new_skill = Skill(
            name=final_name,
            description=candidate_dict.get("description", ""),
            when_to_use=candidate_dict.get("when_to_use", []),
            instructions=candidate_dict.get("instructions", ""),
            version=new_version,
            status="exploration",  # Starts in low-priority/exploration mode
            family=final_family
        )
        
        skill_registry.register_skill(new_skill)
        logger.info(f"[SkillEvolution] Successfully generated and registered '{final_name}' (status=exploration).")

skill_evolution_engine = SkillEvolutionEngine()
