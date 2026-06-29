#!/usr/bin/env python3
"""Reproducible Headroom eval + savings measurement for Alpha IO.

Run:  python tools/headroom_eval.py     (requires `pip install headroom-ai`)

Asserts the accuracy guard (adapter default leaves every represented LLM surface
byte-identical), reports what an aggressive profile would save with a losslessness
verdict, and demonstrates the structural transform that actually delivers on large
structured tool outputs. Skips cleanly if headroom is not installed.
"""
from __future__ import annotations

import json
import re
import sys

CCR = re.compile(r"<<ccr:[0-9a-f]+,[^>]+>>")

SURFACES = {
    "signal_augment": [
        {"role": "system", "content": "You are a trading signal generator."},
        {"role": "user", "content": "Generate new trading alpha signals inspired by these successful examples:\n"
                                    "Asset: ETH, Confidence: 0.82, PnL: 1240\nAsset: SOL, Confidence: 0.77, PnL: 880\n\nNew signals:"},
    ],
    "rag_macro": [
        {"role": "system", "content": "You are a financial macro analyst."},
        {"role": "user", "content": "What is the likely market impact of the next FOMC meeting and current CPI trends?"},
    ],
}


def _verdict(before: str, after: str) -> str:
    if before == after:
        return "identical"
    if CCR.search(after):
        return "ccr-elision"
    try:
        return "lossless-json" if json.loads(before) == json.loads(after) else "reshaped"
    except Exception:
        return "reshaped"


def main() -> int:
    try:
        import headroom
        from headroom import compress, CompressConfig
    except Exception as exc:  # noqa: BLE001
        print(f"headroom not installed ({exc}); skipping eval (pip install headroom-ai)")
        return 0

    default = CompressConfig(compress_user_messages=False, compress_system_messages=False,
                             protect_recent=2, protect_analysis_context=True,
                             min_tokens_to_compress=400)
    aggressive = CompressConfig(compress_user_messages=True, compress_system_messages=True,
                                protect_recent=0, min_tokens_to_compress=10)

    print(f"headroom v{getattr(headroom, '__version__', '?')}\n")
    failures = 0

    print("== ACCURACY GUARD (adapter default must be byte-identical) ==")
    for name, msgs in SURFACES.items():
        res = compress(msgs, model="gpt-4", model_limit=128000, config=default)
        identical = all(a["content"] == b["content"] for a, b in zip(msgs, res.messages))
        print(f"  {name:16s} identical={identical}")
        if not identical:
            failures += 1

    print("\n== SAVINGS (aggressive profile, for reference) ==")
    tb = ta = 0
    for name, msgs in SURFACES.items():
        res = compress(msgs, model="gpt-4", model_limit=128000, config=aggressive)
        tb += res.tokens_before; ta += res.tokens_after
        verdicts = [_verdict(a["content"], b["content"]) for a, b in zip(msgs, res.messages)]
        print(f"  {name:16s} {res.tokens_before:4d}->{res.tokens_after:4d} "
              f"({res.compression_ratio*100:4.1f}%)  {verdicts}")
    print(f"  TOTAL            {tb:4d}->{ta:4d} ({(tb-ta)/tb*100 if tb else 0:4.1f}%)")

    print("\n== CAPABILITY (rag_macro's documented future: many retrieved records) ==")
    records = [{"doc": n, "cpi_yoy": round(3.1 + 0.03 * n, 2), "dxy": round(104 + 0.2 * n, 1),
                "ten_yr": round(4.1 + 0.01 * n, 2), "lean": "hawkish" if n % 2 else "dovish",
                "src": 1000 + n} for n in range(60)]
    big = [{"role": "user", "content": json.dumps({"retrieved_macro_context": records})}]
    res = compress(big, model="gpt-4", model_limit=128000, config=aggressive)
    print(f"  macro-records(60)  {res.tokens_before:5d}->{res.tokens_after:5d} "
          f"({res.compression_ratio*100:4.1f}%)  ccr={bool(CCR.search(res.messages[0]['content']))}  "
          f"{res.transforms_applied}")

    print(f"\n{'PASS' if failures == 0 else 'FAIL'}: accuracy guard ({failures} regression(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
