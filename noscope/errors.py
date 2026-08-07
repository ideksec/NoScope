"""Turn provider/SDK exceptions into actionable messages.

A run that dies on a raw ``AuthenticationError`` traceback tells the user
nothing about what to do next. These helpers map the failures that actually
happen in practice — bad key, wrong model id, rate limit, network — onto a
one-line diagnosis plus the fix.

Matching is duck-typed (exception class name + HTTP status) rather than by
importing provider exception hierarchies, so it works for both the Anthropic
and OpenAI SDKs and doesn't break when either reorganizes its exports.
"""

from __future__ import annotations

# Env var to suggest per provider.
_KEY_HINT = {
    "anthropic": "NOSCOPE_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY)",
    "openai": "NOSCOPE_OPENAI_API_KEY (or OPENAI_API_KEY)",
}


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def explain_error(exc: BaseException, provider: str = "anthropic", model: str = "") -> str | None:
    """Return an actionable explanation, or None if the error isn't recognized."""
    name = type(exc).__name__
    status = _status_code(exc)
    key_hint = _KEY_HINT.get(provider, "your API key")

    if name == "AuthenticationError" or status == 401:
        return (
            f"The {provider} API rejected your credentials.\n"
            f"  Fix: check {key_hint} is set correctly and not expired.\n"
            f"  Verify with: uv run noscope doctor --live"
        )

    if name == "PermissionDeniedError" or status == 403:
        return (
            f"The {provider} API key lacks permission for this request"
            + (f" (model: {model})." if model else ".")
            + "\n  Fix: confirm the key's workspace has access to that model."
        )

    if name == "NotFoundError" or status == 404:
        return (
            f"The model {model or '(unset)'} was not found on {provider}.\n"
            "  Fix: pass a current model with --model, e.g.\n"
            "       --model claude-sonnet-5   (anthropic)\n"
            "       --model gpt-5.6-terra     (openai)"
        )

    if name == "RateLimitError" or status == 429:
        return (
            "Rate limited by the provider.\n"
            "  Fix: wait a moment, lower --workers, or use a smaller timebox.\n"
            "  The SDK already retries automatically; this means retries were exhausted."
        )

    if status == 529 or "overloaded" in str(exc).lower():
        return (
            "The provider is overloaded right now.\n"
            "  Fix: retry shortly — this is a transient upstream condition."
        )

    if name in ("APIConnectionError", "APITimeoutError") or status is not None and status >= 500:
        return (
            "Could not reach the provider (network or upstream error).\n"
            "  Fix: check your connection/proxy, then retry."
        )

    if name == "BadRequestError" or status == 400:
        return (
            f"The provider rejected the request as invalid (model: {model or 'default'}).\n"
            "  This usually means the model doesn't support a parameter NoScope sent.\n"
            "  Fix: try the default model, or report this with the message below."
        )

    return None


def format_run_error(exc: BaseException, provider: str = "anthropic", model: str = "") -> str:
    """A user-facing block for a run-ending error: diagnosis when we have one."""
    explanation = explain_error(exc, provider=provider, model=model)
    if explanation:
        return f"{explanation}\n\n  Details: {type(exc).__name__}: {exc}"
    return f"{type(exc).__name__}: {exc}"
