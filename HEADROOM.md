# Headroom Integration — Agentic_Trading_OS (Alpha IO)

Integration of [Headroom](https://github.com/chopratejas/headroom) (`headroom-ai`
0.27.0, Apache-2.0), the context-compression layer for LLM apps, to reduce token
usage **with no accuracy regression**.

> **Bottom line, stated honestly:** Headroom is wired in correctly, fully
> reversible, and **off by default** so prompts and outputs are byte-identical out
> of the box (zero accuracy loss by construction). Alpha IO's LLM call sites send
> **short prompts** (and use the legacy pre-1.0 OpenAI API), so the *measured* token
> savings on the real surfaces are **~0%**. Headroom's real value (50–60%, lossless)
> only materializes on **large structured retrieved context** — which is the
> *documented future* of `rag_macro` (vector DB), not its current placeholder. The
> adapter is already wired at that exact seam, ready for when the RAG path lands.

## Environment

- Python **3.13.12** in a dedicated venv (`headroom-ai[all]`), per the 3.13 (not
  3.14+) requirement.
- `headroom doctor`: **proxy ✓ pass, version ✓ pass**. The `claude/codex/shell-env`
  warns are intentional — this session's provider routing was not rerouted.

## What was wired

| File | Change |
|---|---|
| `Alpha IO/utils/headroom_compress.py` | **New.** Opt-in, lazy, degrade-to-passthrough adapter. Disabled ⇒ identity. Enabled via `ALPHAIO_HEADROOM=1`. Records per-call savings as JSONL. |
| `Alpha IO/core/signal_augment.py` | Compress the message payload before the synthetic-signal `ChatCompletion` call. **No-op unless enabled.** |
| `Alpha IO/utils/rag_macro.py` | Compress the macro-analyst payload. This is the **future RAG seam** — when the vector DB lands, the retrieved chunks are exactly where Headroom pays off. **No-op unless enabled.** |
| `Alpha IO/tests/test_headroom_compress.py` | **New.** Asserts disabled=identity, env toggles, graceful degradation without the dependency, shape preservation. |
| `Alpha IO/tools/headroom_eval.py` | **New.** Reproducible accuracy guard + savings/losslessness report + capability demo. |
| `Alpha IO/requirements-headroom.txt` | **New.** Optional dependency (not required to run). |
| `Alpha IO/.env.headroom.example` | **New.** All settings, all off by default. |

## Modes evaluated

- **Library (inline `compress()`):** wired opt-in at `signal_augment` and `rag_macro`.
- **Proxy:** stood up and healthy; documented as an optional zero-code route
  (`OPENAI_BASE_URL=http://127.0.0.1:8787/v1`) in `.env.headroom.example`, not
  hard-wired. `--mode cache` recommended (prefix-cache stabilization).
- **MCP / agent wrap / learn:** verified the commands; documented for the operator's
  real environment. Not applied to the live session's global config (non-disruptive;
  would not persist in this ephemeral container).

## Measured results (input-context, deterministic)

From `Alpha IO/tools/headroom_eval.py`:

```
== ACCURACY GUARD (adapter default must be byte-identical) ==
  signal_augment   identical=True
  rag_macro        identical=True
== SAVINGS (aggressive profile, for reference) ==
  signal_augment    65->65 (0.0%)
  rag_macro         43->43 (0.0%)
  TOTAL            108->108 (0.0%)
== CAPABILITY (rag_macro's documented future: many retrieved records) ==
  macro-records(60)  1726->642 (62.8%)  ccr=False  [router:smart_crusher]
PASS: accuracy guard (0 regression(s))
```

- **Real surfaces: 0% change** — the prompts are too short and protected to compress.
- **Capability demo: 62.8% lossless** on 60 structured macro records (columnar
  reshape; all records/fields preserved). This is precisely what `rag_macro` will
  produce once it retrieves real macro context — and the adapter is already there.

Output-token holdout (`HEADROOM_OUTPUT_HOLDOUT`) is not measurable here (no live
provider: no API keys, egress proxy blocks the upstream).

## Accuracy: how "zero loss" is guaranteed

1. **Default off ⇒ identity.** The adapter returns the same object when disabled; the
   unit test and the eval guard assert byte-identical output.
2. **CCR caveat:** structural transforms are lossless, but long/low-entropy strings
   may be replaced by a reversible `<<ccr:…>>` pointer recoverable only out-of-band
   (`headroom_retrieve` / proxy). So enabling compression on a one-shot decision call
   with no retrieval round-trip *can* change model input — hence default-off on
   decision paths, and pair with proxy + MCP when enabled.

## Rollback

- **Library:** inert by default; `git revert` the integration commit, or never set
  `ALPHAIO_HEADROOM`.
- **Proxy/agent wrap:** `headroom unwrap codex`, `headroom mcp uninstall`, unset
  `OPENAI_BASE_URL`. None applied globally in this session.

## Surfaces left uncovered, and why

- **Legacy OpenAI call style** — `signal_augment`/`rag_macro` use the pre-1.0
  `openai.ChatCompletion`/`openai.Embedding` API (incompatible with the pinned
  `openai>=1.0`). The compression adapter is wired at the message level and is
  agnostic to that, but these paths are placeholder/legacy; the real win arrives with
  the planned RAG/vector-DB rewrite of `rag_macro`.
- **Embeddings (`text-embedding-3-small`)** — short text, nothing to compress.
- **Neural text compressor (Kompress-v2-base)** — ONNX artifact failed to load in
  this environment; only structural transforms ran.
- **Output-token holdout / `headroom learn`** — require live provider access this
  sandbox lacks; documented for the real environment.
