"""
The agents: a toolset, a prompt and a model, composed with deepagents.

Two of them, and the difference between them is the whole architecture. The **coordination**
agent answers a person's questions and proposes actions over needs and offers. The
**frontier** agent decides where to look next and can reach no post through any tool it
holds.

Note:
    Nothing here contains a domain rule. Prompts carry *behaviour* — read the balance
    first, do not translate the catalog — and every guarantee about what may be written
    lives in the service layer, where a shell session is bound by it too.
"""

from agent_tools.agents.build import Coordinates, build_agent, render_prompt
from agent_tools.agents.llm import LLMNotConfigured, build_chat_model
from agent_tools.agents.streaming import stream_agent, translate_chunk

__all__ = [
    "Coordinates",
    "LLMNotConfigured",
    "build_agent",
    "build_chat_model",
    "render_prompt",
    "stream_agent",
    "translate_chunk",
]
