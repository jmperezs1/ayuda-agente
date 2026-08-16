"""
The chat model the agents run on.

Thin on purpose. Which model each job gets is decided once, in `ayudagente.radar.llm`, and
this module only wraps that choice in the LangChain interface deepagents needs. The rest of
the system talks to OpenAI through the raw SDK; agents need a `BaseChatModel`, and that is
the whole difference.

Note:
    Reading the role map rather than its own variable is the point. A second `OPENAI_MODEL`
    here would drift from `OPENAI_MODEL_REASONING` the first time somebody changed one of
    them, and the symptom — the agent quietly answering on a different model than the rest
    of the pipeline — is invisible until the bill or the quality says so.

    The Responses API is set here rather than left to deepagents. Its OpenAI provider
    profile turns it on, but only while resolving a model *string*; this module hands
    `create_deep_agent` a built instance, so the profile never runs and the default endpoint
    survives. Missing it costs a 400 on the agent's first tool call, not at startup.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from langchain_core.language_models import BaseChatModel

from ayudagente.radar.llm import Role, model_for

REQUEST_TIMEOUT_SECONDS = 120
# Reasoning models reject a temperature; only these accept one
TEMPERATURE_CAPABLE_PREFIXES = ("gpt-4", "gpt-3.5")


class LLMNotConfigured(RuntimeError):
    """Credentials or a model name are missing. Raised at build time, never mid-turn."""


def accepts_temperature(model_name: str) -> bool:
    """
    Report whether a model takes a `temperature`.

    Returns:
        bool: False for reasoning models, which reject the parameter outright rather than
            ignoring it. Sending it turns every agent call into a 400.
    """
    return model_name.startswith(TEMPERATURE_CAPABLE_PREFIXES)


def build_chat_model(role: Role = Role.REASONING) -> BaseChatModel:
    """
    Build the chat model an agent runs on.

    Args:
        role (Role): Which model tier to use. Agents are judgment, so reasoning by default.

    Returns:
        BaseChatModel: Configured from `OPENAI_API_KEY` and `OPENAI_MODELS`.

    Raises:
        LLMNotConfigured: When the key or the role's model name is missing. Both are
            reported here rather than at request time, so a missing variable surfaces as a
            503 before the stream opens instead of an error event halfway through a turn.
    """
    if not settings.OPENAI_API_KEY:
        raise LLMNotConfigured("missing environment variable: OPENAI_API_KEY")

    try:
        model_name = model_for(role)
    except ImproperlyConfigured as exc:
        raise LLMNotConfigured(str(exc)) from exc

    from langchain_openai import ChatOpenAI

    options: dict = {
        "model": model_name,
        "api_key": settings.OPENAI_API_KEY,
        "timeout": REQUEST_TIMEOUT_SECONDS,
        "max_retries": 2,
        "use_responses_api": True,  # chat/completions rejects tools on a reasoning model
    }

    if accepts_temperature(model_name):
        # Low: these agents pick between concrete options, they do not need variety
        options["temperature"] = 0.2

    return ChatOpenAI(**options)
