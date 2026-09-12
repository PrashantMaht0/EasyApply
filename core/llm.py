"""How every agent talks to a model. Provider choice, a JSON guarded call, and fencing."""

import os

from strands.types.exceptions import MaxTokensReachedException

from core.verify import BudgetExceeded, parse_json, schema_guard

DEFAULT_BEDROCK_MODEL_ID = "global.amazon.nova-2-lite-v1:0"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL_ID = "llama3.2"
# Caps generation. A high cap lets a small model loop on repeated output instead of closing the JSON
MAX_TOKENS = 1024
SCORER_MAX_TOKENS = 4096


def get_model(tier: str = "default"):
    """Bedrock by default, ollama for local work with no AWS credentials.

    tier is "scorer" for the high volume stage, which uses the cheaper model.
    """
    provider = os.getenv("MODEL_PROVIDER", "bedrock").lower()
    if provider == "ollama":
        from strands.models.ollama import OllamaModel

        return OllamaModel(
            host=os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST,
            model_id=os.getenv("OLLAMA_MODEL_ID") or DEFAULT_OLLAMA_MODEL_ID,
            temperature=0,
            max_tokens=SCORER_MAX_TOKENS if tier == "scorer" else MAX_TOKENS,
            # ollama JSON mode, so a local model cannot wrap the object in prose
            additional_args={"format": "json"},
        )
    if provider != "bedrock":
        raise ValueError(f"MODEL_PROVIDER must be bedrock or ollama, got {provider!r}")

    from strands.models import BedrockModel

    default_id = os.getenv("BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID
    model_id = os.getenv("BEDROCK_SCORER_MODEL_ID") or default_id if tier == "scorer" else default_id
    return BedrockModel(
        model_id=model_id,
        region_name=os.getenv("AWS_REGION") or "us-east-1",
        temperature=0,
        max_tokens=SCORER_MAX_TOKENS if tier == "scorer" else MAX_TOKENS,
    )


def model_id_of(model) -> str:
    """The id the provider actually resolved, used for cost accounting."""
    return model.get_config().get("model_id", "")


def _usage(source) -> tuple[int, int, float]:
    """Tokens and wall time from a result, or from the agent itself after a capped call."""
    try:
        metrics = getattr(source, "metrics", None) or source.event_loop_metrics
        summary = metrics.get_summary()
        usage = summary.get("accumulated_usage", {})
        return (int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0)),
                float(summary.get("total_duration", 0.0)))
    except (AttributeError, TypeError, ValueError):
        return 0, 0, 0.0


JSON_REMINDER = (
    "\n\nYour previous reply was not valid JSON matching the required shape. "
    "Reply with the JSON object only, no prose and no code fence."
)


def run_json(make_agent, prompt: str, spec: dict, model_id: str, guard=None):
    """Calls an agent and returns parsed JSON that passed V4. One retry, then None.

    make_agent is called per attempt, so a failed partial reply cannot pollute the retry.
    """
    for attempt in range(2):
        if guard is not None:
            guard.check()
        agent = make_agent()
        result, text = None, ""
        try:
            result = agent(prompt if attempt == 0 else prompt + JSON_REMINDER)
            text = str(result)
        except BudgetExceeded:
            raise
        except MaxTokensReachedException:
            # Strands raises rather than returning, so the partial reply is read back here
            text = last_assistant_text(agent)
        except Exception:
            text = ""
        if guard is not None:
            # usage stays on the agent, so a capped or failed attempt is charged like any other
            input_tokens, output_tokens, seconds = _usage(result or agent)
            guard.record(model_id, input_tokens, output_tokens,
                         agent=getattr(agent, "name", ""), seconds=seconds)
        parsed = parse_json(text)
        if parsed is not None and schema_guard(parsed, spec):
            return parsed
    return None


def last_assistant_text(agent) -> str:
    """The most recent assistant turn, including one cut short by the token cap."""
    for message in reversed(getattr(agent, "messages", [])):
        if message.get("role") != "assistant":
            continue
        return "".join(block.get("text", "") for block in message.get("content", []))
    return ""


def delimit(untrusted: str, label: str = "user_text") -> str:
    """Free text is data, never instructions. Wrapped so an injected order is visible as content."""
    cleaned = (untrusted or "").replace(f"</{label}>", "")
    return f"<{label}>\n{cleaned}\n</{label}>"
