"""
Single LLM access point for the openai>=1.0 SDK.

Three call sites (``core.signal_augment``, ``core.signal_memory``,
``utils.rag_macro``) were written against the pre-1.0 ``openai.ChatCompletion`` /
``openai.Embedding`` API, which the pinned ``openai>=1.0`` removed. Every one of
them raised on first call. This module replaces all three with one lazily
constructed client.

Two properties matter:

- **Import is free.** Importing a module that uses this must not require the SDK
  or a key. Only invocation does. Modules are imported by the test suite and by
  ``compileall`` in CI, neither of which has credentials.
- **Failure is typed.** A missing key or missing SDK raises ``LLMUnavailable``
  with an actionable message, not an ``AttributeError`` from a removed symbol.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_CHAT_MODEL = "gpt-4o"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"
DEFAULT_EMBED_DIM = 1536


class LLMUnavailable(RuntimeError):
    """Raised when an LLM call is attempted without a usable client.

    Carries the reason so callers can distinguish "not configured" (an operator
    problem) from "call failed" (a runtime problem).
    """


_client_lock = threading.Lock()
_client: Optional[Any] = None
_client_key: Optional[str] = None


def api_key() -> Optional[str]:
    """The configured key, or None. Read at call time, never at import."""
    key = os.getenv("OPENAI_API_KEY")
    return key.strip() if key and key.strip() else None


def is_configured() -> bool:
    """Whether an LLM call could succeed. Cheap; does not construct a client."""
    return api_key() is not None


def get_client() -> Any:
    """Return a cached ``openai.OpenAI`` client, building it on first use.

    Rebuilds if the key changed, so a process that loads credentials late (as
    ``core.credentials`` allows) does not hold a stale client.
    """
    global _client, _client_key

    key = api_key()
    if key is None:
        raise LLMUnavailable(
            "OPENAI_API_KEY is not set. Set it in the environment or via "
            "core.credentials before using an LLM-backed path."
        )

    with _client_lock:
        if _client is not None and _client_key == key:
            return _client

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMUnavailable(
                "The openai package (>=1.0) is not installed. "
                "Install it with: pip install 'openai>=1.0'"
            ) from exc

        _client = OpenAI(api_key=key)
        _client_key = key
        return _client


def reset_client() -> None:
    """Drop the cached client. Used by tests and after a credential rotation."""
    global _client, _client_key
    with _client_lock:
        _client = None
        _client_key = None


def chat(
    messages: Sequence[Dict[str, str]],
    model: str = DEFAULT_CHAT_MODEL,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """One chat completion, returning the message text.

    Replaces ``openai.ChatCompletion.create(...)["choices"][0]["message"]["content"]``.
    """
    if not messages:
        raise ValueError("messages must be non-empty")

    kwargs: Dict[str, Any] = {"model": model, "messages": list(messages)}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    try:
        response = get_client().chat.completions.create(**kwargs)
    except LLMUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - SDK raises a wide, version-dependent set
        raise LLMUnavailable(f"chat completion failed: {type(exc).__name__}: {exc}") from exc

    return response.choices[0].message.content or ""


def embed(text: str, model: str = DEFAULT_EMBED_MODEL) -> List[float]:
    """One embedding vector.

    Replaces ``openai.Embedding.create(...)["data"][0]["embedding"]``.
    """
    if not text or not text.strip():
        raise ValueError("text must be non-empty")

    try:
        response = get_client().embeddings.create(input=text, model=model)
    except LLMUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - SDK raises a wide, version-dependent set
        raise LLMUnavailable(f"embedding failed: {type(exc).__name__}: {exc}") from exc

    return list(response.data[0].embedding)
