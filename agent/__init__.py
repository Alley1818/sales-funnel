"""
AI Agent module — intelligent WhatsApp agent with memory and tools.

Components:
- AIBrain: LLM integration with tool calling
- ContextManager: Lead context and conversation state
- AgentTools: Tools for sending KP, scheduling follow-ups, etc.
- FollowUpScheduler: Automated follow-up reminders
- VectorMemory: ChromaDB integration for semantic search
"""
from agent.memory import VectorMemory
from agent.context import ContextManager
from agent.tools import AgentTools
from agent.scheduler import FollowUpScheduler
from agent.brain import AIBrain

# Singleton instances
_memory: VectorMemory = None
_brain: AIBrain = None
_scheduler: FollowUpScheduler = None


def get_memory() -> VectorMemory:
    global _memory
    if _memory is None:
        _memory = VectorMemory()
    return _memory


def get_brain() -> AIBrain:
    global _brain
    if _brain is None:
        _brain = AIBrain(get_memory())
    return _brain


def get_scheduler() -> FollowUpScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = FollowUpScheduler(get_memory())
    return _scheduler


def start_scheduler():
    """Start the follow-up scheduler."""
    scheduler = get_scheduler()
    scheduler.start()


def process_incoming_message(lead_id: int, lead_data: dict, message: str) -> dict:
    """Process an incoming WhatsApp message."""
    brain = get_brain()
    return brain.process_message(lead_id, lead_data, message)


__all__ = [
    'VectorMemory', 'ContextManager', 'AgentTools', 'FollowUpScheduler', 'AIBrain',
    'get_memory', 'get_brain', 'get_scheduler', 'start_scheduler',
    'process_incoming_message',
]
