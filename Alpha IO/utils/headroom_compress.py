"""Opt-in, reversible context compression via Headroom (github.com/chopratejas/headroom).

Headroom shrinks the token footprint of LLM message payloads before they reach a
provider. The LLM call sites in Alpha IO (synthetic-signal generation in
``core.signal_augment`` and the macro RAG prompt in ``utils.rag_macro``) feed a
*decision* model, so compression is wired as a **strictly opt-in, default-off
passthrough**:

  * Disabled (default) -> :func:`compress_messages` returns the same ``messages``
    list unchanged. Behaviour and outputs are identical, so accuracy is unaffected
    out of the box.
  * Enabled (``ALPHAIO_HEADROOM=1``) -> messages run through Headroom's pipeline
    with a conservative config; per-call savings are recorded for measurement.

Accuracy caveat (see HEADROOM.md): Headroom's structural transforms (JSON
minification, dedup) are lossless, but long/low-entropy strings may be swapped for
a reversible ``<<ccr:...>>`` pointer that is recoverable only out-of-band
(``headroom_retrieve`` / the proxy). A plain provider call therefore sees a
placeholder, not the elided text -- which is exactly why this stays off by default
on decision paths and should only be enabled behind a retrieval-capable surface
(e.g. ``headroom proxy``) or on genuinely high-volume, non-decision context.

The ``headroom`` import is lazy and any failure degrades to passthrough, so this
module never becomes a hard dependency and never breaks a call.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

Messages = List[Dict[str, Any]]

_ENABLE_ENVS = ("ALPHAIO_HEADROOM", "HEADROOM_ENABLED")
_LOG_ENV = "ALPHAIO_HEADROOM_LOG"
_DEFAULT_LOG = os.path.expanduser("~/.headroom/alphaio_savings.jsonl")

_DEFAULT_MODEL = "gpt-4"
_DEFAULT_LIMIT = 128_000


def compression_enabled() -> bool:
    """True only if an operator explicitly opted in via env."""
    return any(os.environ.get(e, "").strip().lower() in ("1", "true", "yes", "on")
               for e in _ENABLE_ENVS)


def _record(surface: str, before: int, after: int, transforms: Any) -> None:
    path = os.environ.get(_LOG_ENV, _DEFAULT_LOG)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.time(), "surface": surface,
                "tokens_before": before, "tokens_after": after,
                "tokens_saved": before - after,
                "transforms": list(transforms) if transforms else [],
            }) + "\n")
    except Exception:  # noqa: BLE001 - measurement must never affect trading
        pass


def compress_messages(messages: Messages, *, surface: str,
                      model: str = _DEFAULT_MODEL, model_limit: int = _DEFAULT_LIMIT,
                      compress_user_messages: bool = False,
                      min_tokens_to_compress: int = 400) -> Messages:
    """Return Headroom-compressed messages when opted in, else the input unchanged.

    With the feature disabled this is the identity function (same object), so there
    is no behaviour change. Any error degrades to the original messages.
    """
    if not compression_enabled():
        return messages
    try:
        import headroom  # lazy: optional, heavy dependency
    except Exception:  # noqa: BLE001 - optional dependency
        return messages
    try:
        config = headroom.CompressConfig(
            compress_user_messages=compress_user_messages,
            compress_system_messages=False,
            protect_recent=2,
            protect_analysis_context=True,
            min_tokens_to_compress=min_tokens_to_compress,
        )
        result = headroom.compress(messages, model=model, model_limit=model_limit, config=config)
        _record(surface, result.tokens_before, result.tokens_after, result.transforms_applied)
        return result.messages
    except Exception:  # noqa: BLE001 - a compressor fault must not break the call
        return messages


__all__ = ["compress_messages", "compression_enabled"]
