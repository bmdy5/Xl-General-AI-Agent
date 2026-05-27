from .base import on_session_end, audit_tool_call
from .fatigue import inject_fatigue_prompt_if_needed
from .memory import select_relevant_memories, is_preference_query, filter_memories_by_relevance

# Re-exports for backward compatibility
from .coach import PENDING_DIR
from .tester import SandboxToolRegistry, generate_test_prompt, run_llm_judge, run_self_test
from .traces import get_today_traces, get_recent_corrections, TRACES_DIR, record_tool_call
from .apply import EvolutionEngine, on_session_start
from .dream import trigger_deep_dream_evolution
