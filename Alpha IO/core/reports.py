"""
Report layer — the morning macro brief.

Composes what the system already knows into the one artifact a desk actually
reads before the open: what moved overnight, what the book looks like, what the
risk posture is, and which signals are uncrowded enough to be worth attention.

Two design rules:

- **Deterministic by default.** The same corpus and the same ledger snapshot
  produce byte-identical markdown. A brief you cannot diff is a brief you cannot
  trust, and determinism is what makes the tests meaningful.
- **Inference is opt-in.** ``ALPHAIO_LLM_BRIEF=1`` adds a synthesis paragraph on
  top; unset, nothing calls a model and the brief costs nothing. Same pattern as
  the Headroom adapter.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DEFAULT_REPORT_DIR = "data/reports"
LLM_BRIEF_ENV = "ALPHAIO_LLM_BRIEF"


# =============================================================================
# Sections
# =============================================================================

@dataclass
class TermDigest:
    """One term's overnight footprint in the corpus."""
    term: str
    mentions: int
    sources: int
    crowd_sentiment: float

    @property
    def lean(self) -> str:
        if self.crowd_sentiment >= 0.65:
            return "bullish"
        if self.crowd_sentiment <= 0.35:
            return "bearish"
        return "mixed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "mentions": self.mentions,
            "sources": self.sources,
            "crowd_sentiment": round(self.crowd_sentiment, 4),
            "lean": self.lean,
        }


@dataclass
class Candidate:
    """An uncrowded term worth a look, with the measurement behind it."""
    term: str
    score: float
    mentions: int
    sources: int
    crowd_sentiment: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "score": round(self.score, 4),
            "mentions": self.mentions,
            "sources": self.sources,
            "crowd_sentiment": round(self.crowd_sentiment, 4),
        }


@dataclass
class MorningBrief:
    """A rendered brief plus the structured data behind it."""
    generated_at: datetime
    headlines: int
    sources: List[str]
    digest: List[TermDigest] = field(default_factory=list)
    candidates: List[Candidate] = field(default_factory=list)
    book: Dict[str, Any] = field(default_factory=dict)
    risk: Dict[str, Any] = field(default_factory=dict)
    synthesis: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return f"macro-{self.generated_at.strftime('%Y%m%d')}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "headlines": self.headlines,
            "sources": self.sources,
            "digest": [d.to_dict() for d in self.digest],
            "candidates": [c.to_dict() for c in self.candidates],
            "book": self.book,
            "risk": self.risk,
            "synthesis": self.synthesis,
            "notes": self.notes,
        }

    def to_markdown(self) -> str:
        stamp = self.generated_at.strftime("%Y-%m-%d %H:%M UTC")
        lines: List[str] = [f"# Morning Brief — {stamp}", ""]

        lines.append(self._overnight_line())
        lines.append("")

        lines.append("## Overnight tape")
        lines.append("")
        if self.digest:
            lines.append("| Term | Mentions | Sources | Lean |")
            lines.append("| --- | ---: | ---: | --- |")
            for entry in self.digest:
                lines.append(
                    f"| {entry.term} | {entry.mentions} | {entry.sources} | {entry.lean} |"
                )
        else:
            lines.append("No terms cleared the mention threshold in the window.")
        lines.append("")

        lines.append("## Uncrowded candidates")
        lines.append("")
        if self.candidates:
            lines.append(
                "Ranked by asymmetry — high confidence against low coverage. "
                "A candidate here is a question, not a position."
            )
            lines.append("")
            lines.append("| Term | Asymmetry | Mentions | Sources | Crowd |")
            lines.append("| --- | ---: | ---: | ---: | ---: |")
            for entry in self.candidates:
                lines.append(
                    f"| {entry.term} | {entry.score:.4f} | {entry.mentions} | "
                    f"{entry.sources} | {entry.crowd_sentiment:.2f} |"
                )
        else:
            lines.append("Nothing uncrowded enough to flag.")
        lines.append("")

        lines.append("## Book")
        lines.append("")
        lines.extend(self._book_lines())
        lines.append("")

        lines.append("## Risk posture")
        lines.append("")
        lines.extend(self._risk_lines())
        lines.append("")

        if self.synthesis:
            lines.append("## Synthesis")
            lines.append("")
            lines.append(self.synthesis)
            lines.append("")

        if self.notes:
            lines.append("## Notes")
            lines.append("")
            lines.extend(f"- {note}" for note in self.notes)
            lines.append("")

        lines.append("---")
        lines.append(
            "Generated from the ingestion corpus and the canonical ledger. "
            "Nothing in this brief routes an order."
        )
        return "\n".join(lines) + "\n"

    def _overnight_line(self) -> str:
        if not self.headlines:
            return "No documents ingested in the window — check feed health before trusting this brief."
        source_count = len(self.sources)
        return (
            f"{self.headlines} document{'s' if self.headlines != 1 else ''} "
            f"across {source_count} source{'s' if source_count != 1 else ''} in the window."
        )

    def _book_lines(self) -> List[str]:
        if not self.book:
            return ["No ledger attached."]
        positions = self.book.get("positions") or {}
        if not positions:
            return [
                f"Flat. Cash {self.book.get('cash', 0.0):,.2f}, "
                f"realized P&L {self.book.get('realized_pnl', 0.0):,.2f}."
            ]

        lines = [
            f"Cash {self.book.get('cash', 0.0):,.2f} · "
            f"realized P&L {self.book.get('realized_pnl', 0.0):,.2f} · "
            f"total exposure {self.book.get('total_exposure', 0.0):,.2f}",
            "",
            "| Symbol | Quantity | Exposure |",
            "| --- | ---: | ---: |",
        ]
        for symbol in sorted(positions):
            position = positions[symbol]
            lines.append(
                f"| {symbol} | {position.get('quantity', 0):,.4f} | "
                f"{position.get('exposure', 0.0):,.2f} |"
            )
        open_orders = self.book.get("open_orders", 0)
        if open_orders:
            lines.extend(["", f"{open_orders} order(s) still working."])
        return lines

    def _risk_lines(self) -> List[str]:
        if not self.risk:
            return ["No risk manager attached."]
        return [f"- **{key}**: {value}" for key, value in sorted(self.risk.items())]


# =============================================================================
# Generation
# =============================================================================

class BriefGenerator:
    """Builds a ``MorningBrief`` from the corpus, the ledger and the risk engine.

    Every collaborator is optional and duck-typed. A brief with only a corpus is
    still a useful brief; one with nothing attached says so rather than
    fabricating numbers.
    """

    def __init__(
        self,
        corpus: Optional[Any] = None,
        ledger: Optional[Any] = None,
        risk_manager: Optional[Any] = None,
        asymmetry_index: Optional[Any] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        report_dir: str = DEFAULT_REPORT_DIR,
    ) -> None:
        self.corpus = corpus
        self.ledger = ledger
        self.risk_manager = risk_manager
        self.asymmetry_index = asymmetry_index
        self._clock = clock
        self.report_dir = Path(report_dir)

    # -- public ------------------------------------------------------------

    def generate(
        self,
        now: Optional[datetime] = None,
        top_n: int = 10,
        min_mentions: int = 2,
        candidate_confidence: float = 0.8,
    ) -> MorningBrief:
        now = now or self._clock()
        notes: List[str] = []

        items = self.corpus.recent(now) if self.corpus is not None else []
        if self.corpus is None:
            notes.append("No corpus attached — overnight sections are empty.")

        counts: Dict[str, int] = {}
        for item in items:
            for entity in item.entities:
                counts[entity] = counts.get(entity, 0) + 1

        # Sort by mentions desc, then term asc, so ties are stable and the
        # brief is diffable across runs.
        ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

        digest = [
            TermDigest(
                term=term,
                mentions=count,
                sources=len({i.source for i in self.corpus.matches(term, now)}),
                crowd_sentiment=self.corpus.crowd_sentiment(term, now),
            )
            for term, count in ranked[:top_n]
            if count >= min_mentions
        ]

        candidates = self._candidates(ranked, now, candidate_confidence, top_n)

        book, book_note = self._book()
        if book_note:
            notes.append(book_note)

        risk, risk_note = self._risk()
        if risk_note:
            notes.append(risk_note)

        brief = MorningBrief(
            generated_at=now,
            headlines=len(items),
            sources=sorted({item.source for item in items}),
            digest=digest,
            candidates=candidates,
            book=book,
            risk=risk,
            notes=notes,
        )

        brief.synthesis = self._synthesize(brief)
        return brief

    def write(self, brief: MorningBrief, report_dir: Optional[str] = None) -> Path:
        """Write the brief to ``<report_dir>/macro-YYYYMMDD.md``."""
        directory = Path(report_dir) if report_dir else self.report_dir
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{brief.slug}.md"
        path.write_text(brief.to_markdown(), encoding="utf-8")
        return path

    def run(self, now: Optional[datetime] = None, notifier: Optional[Any] = None) -> Dict[str, Any]:
        """Generate, write, and optionally announce. Shaped for the scheduler."""
        brief = self.generate(now=now)
        path = self.write(brief)
        if notifier is not None:
            self._announce(brief, path, notifier)
        return {"path": str(path), "headlines": brief.headlines,
                "candidates": len(brief.candidates)}

    # -- internals ---------------------------------------------------------

    def _candidates(self, ranked, now, confidence: float, top_n: int) -> List[Candidate]:
        """Score every seen term for asymmetry and keep the highest.

        Uses the measured path, so a candidate's score reflects what the corpus
        actually saw rather than a default.
        """
        if self.corpus is None:
            return []

        index = self.asymmetry_index
        if index is None:
            try:
                from core.asymmetry_index import AsymmetryIndex
            except ImportError:
                return []
            index = AsymmetryIndex(corpus=self.corpus)

        scored: List[Candidate] = []
        for term, _ in ranked:
            measurement = index.compute_measured(term, confidence, term=term)
            scored.append(Candidate(
                term=term,
                score=measurement.score,
                mentions=measurement.news_count,
                sources=measurement.sources,
                crowd_sentiment=measurement.crowd_sentiment,
            ))

        scored.sort(key=lambda c: (-c.score, c.term))
        return scored[:top_n]

    def _book(self):
        if self.ledger is None:
            return {}, "No ledger attached — book section is empty."
        try:
            snapshot = self.ledger.snapshot()
        except Exception as exc:  # noqa: BLE001 - a brief must render even when a source is down
            return {}, f"Ledger snapshot failed: {type(exc).__name__}"

        positions = {
            symbol: {"quantity": position.quantity, "exposure": position.exposure}
            for symbol, position in snapshot.positions.items()
            if position.quantity != 0
        }
        return {
            "cash": snapshot.cash,
            "realized_pnl": snapshot.realized_pnl,
            "positions": positions,
            "total_exposure": snapshot.total_exposure,
            "open_orders": len(snapshot.open_orders),
        }, None

    def _risk(self):
        if self.risk_manager is None:
            return {}, None
        for method in ("get_risk_summary", "summary", "get_metrics"):
            getter = getattr(self.risk_manager, method, None)
            if getter is None:
                continue
            try:
                value = getter()
            except Exception as exc:  # noqa: BLE001 - same containment as the ledger
                return {}, f"Risk summary failed: {type(exc).__name__}"
            if isinstance(value, dict):
                return {k: v for k, v in value.items() if isinstance(v, (int, float, str, bool))}, None
            if hasattr(value, "to_dict"):
                raw = value.to_dict()
                return {k: v for k, v in raw.items() if isinstance(v, (int, float, str, bool))}, None
        return {}, "Risk manager exposed no recognized summary method."

    def _synthesize(self, brief: MorningBrief) -> Optional[str]:
        """Optional LLM paragraph. Off unless ``ALPHAIO_LLM_BRIEF=1``.

        Returns None on any failure: a brief that renders without synthesis is
        strictly better than a brief that does not render.
        """
        if os.getenv(LLM_BRIEF_ENV, "").strip() not in ("1", "true", "True"):
            return None
        try:
            from core.llm_client import chat, is_configured
            if not is_configured():
                return None
            digest = "; ".join(
                f"{d.term}: {d.mentions} mentions, {d.lean}" for d in brief.digest[:10]
            ) or "no notable terms"
            return chat([
                {"role": "system", "content": "You are a macro analyst writing a terse pre-open note. Three sentences maximum. No advice."},
                {"role": "user", "content": f"Overnight coverage: {digest}. Total documents: {brief.headlines}."},
            ]).strip() or None
        except Exception:  # noqa: BLE001 - synthesis is a bonus, never a dependency
            return None

    @staticmethod
    def _announce(brief: MorningBrief, path: Path, notifier: Any) -> None:
        """Best-effort hand-off to the alert layer."""
        try:
            notifier.notify(
                title=f"Morning brief {brief.generated_at.strftime('%Y-%m-%d')}",
                message=f"{brief.headlines} documents, {len(brief.candidates)} candidates. {path}",
            )
        except Exception:  # noqa: BLE001 - delivery failure must not fail the report
            pass


def install_corpus_alert_sweep(
    scheduler: Any,
    alert_manager: Any,
    corpus: Any,
    interval_seconds: float = 60.0,
    asymmetry_index: Optional[Any] = None,
    name: str = "corpus-alerts",
) -> Any:
    """Register a recurring corpus alert sweep on a scheduler.

    Sweeping often is safe: cooldown and max-triggers live in the alert, not in
    the cadence, so a one-minute sweep does not mean a one-minute alert.
    """
    from core.scheduler import Interval
    return scheduler.add_job(
        name,
        Interval(interval_seconds),
        lambda: alert_manager.update_from_corpus(corpus, asymmetry_index=asymmetry_index),
    )


def install_morning_brief(
    scheduler: Any,
    generator: BriefGenerator,
    hour: int = 6,
    minute: int = 0,
    notifier: Optional[Any] = None,
    name: str = "morning-brief",
) -> Any:
    """Register the brief on a scheduler at ``hour:minute`` on trading days."""
    from core.scheduler import DailyAt
    return scheduler.add_job(
        name,
        DailyAt(hour=hour, minute=minute),
        lambda: generator.run(notifier=notifier),
    )
