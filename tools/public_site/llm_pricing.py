"""Allowlisted token-price estimates for the admin LLM page.

An unlisted provider or model has no price. Callers must keep that as null
rather than treating it as zero.
"""

from __future__ import annotations

PRICING_VERSION = "2026-10-09"

# Estimated USD per 1,000,000 tokens: (input, output).
_PER_MILLION: dict[tuple[str, str], tuple[float, float]] = {
    ("ollama", "gpt-oss:20b"): (0.0, 0.0),
    ("ollama", "llama3.1:8b"): (0.0, 0.0),
    ("nvidia", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"): (0.05, 0.20),
}


def token_rates(provider: str | None, model: str | None) -> tuple[float, float] | None:
    """Return input and output USD per 1M tokens, or ``None`` when unlisted."""
    if not provider or not model:
        return None
    return _PER_MILLION.get((provider, model))


def estimated_call_cost(
    provider: str | None,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """Estimate one call. Missing tokens or an unknown model stay ``None``."""
    rates = token_rates(provider, model)
    if rates is None or input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000
