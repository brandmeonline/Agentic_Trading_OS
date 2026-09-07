"""Deterministic authority boundary — ATOS-P1-AGENT-001.

Invariant:

    All probabilistic and agentic output passes through one deterministic
    pre-trade policy boundary that no agent can bypass.

Two kinds of test here. The first kind checks the boundary behaves: malformed
proposals die at the door, policy may only shrink a request, agents cannot
double-order.

The second kind is architectural. It reads the source tree and fails if any
module outside the execution boundary calls a broker order method directly.
That test exists because a boundary is only a boundary if there is no way
around it, and "we all agreed to go through the engine" is not enforcement.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.trade_proposal import (  # noqa: E402
    DeterministicTradeBoundary,
    Direction,
    Eligibility,
    ProposalRejected,
    TradeProposal,
    arbitrate,
)

pytestmark = pytest.mark.adversarial

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
ALPHA_IO = Path(__file__).resolve().parents[1]


def proposal(**overrides):
    base = dict(
        instrument="AAPL",
        direction=Direction.BUY,
        confidence=0.8,
        desired_notional=1000.0,
        agent_id="momentum",
        agent_version="v1",
        eligibility=Eligibility.LIVE,
        created_at=NOW,
    )
    base.update(overrides)
    return TradeProposal(**base)


def boundary(**overrides):
    overrides.setdefault("mode", Eligibility.LIVE)
    return DeterministicTradeBoundary(**overrides)


# ---------------------------------------------------------------------------
# Malformed proposals die at the door
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("confidence", [float("nan"), float("inf"),
                                        float("-inf"), 1.5, -0.1, 42.0])
def test_an_impossible_confidence_is_rejected(confidence):
    with pytest.raises(ProposalRejected):
        boundary().normalize(proposal(confidence=confidence))


def test_a_boolean_confidence_is_rejected():
    with pytest.raises(ProposalRejected, match="boolean"):
        boundary().normalize(proposal(confidence=True))


def test_a_negative_size_is_rejected():
    """Direction expresses side; a negative size is a second, conflicting one."""
    with pytest.raises(ProposalRejected, match="negative"):
        boundary().normalize(proposal(desired_notional=-500.0))


@pytest.mark.parametrize("size", [float("nan"), float("inf")])
def test_a_non_finite_size_is_rejected(size):
    with pytest.raises(ProposalRejected):
        boundary().normalize(proposal(desired_notional=size))


def test_a_proposal_with_no_size_is_rejected():
    with pytest.raises(ProposalRejected, match="must express size"):
        boundary().normalize(
            proposal(desired_notional=None, target_exposure=None)
        )


def test_a_missing_instrument_is_rejected():
    with pytest.raises(ProposalRejected, match="no instrument"):
        boundary().normalize(proposal(instrument=""))


def test_a_raw_dictionary_is_not_a_proposal():
    """An agent may not hand the boundary something order-shaped."""
    with pytest.raises(ProposalRejected, match="raw dictionaries"):
        boundary().normalize({"symbol": "AAPL", "qty": 100, "side": "buy"})


def test_a_malformed_proposal_becomes_a_refusal_not_an_exception():
    """evaluate() must not propagate: one bad agent cannot stop the loop."""
    decision = boundary().evaluate(proposal(confidence=float("nan")))
    assert not decision.approved
    assert decision.approved_notional == 0.0
    assert "malformed" in decision.reasons[0]


# ---------------------------------------------------------------------------
# Policy may only shrink
# ---------------------------------------------------------------------------

def test_a_clean_proposal_is_approved():
    decision = boundary().evaluate(proposal())
    assert decision.approved
    assert decision.approved_notional == 1000.0


def test_an_oversized_proposal_is_capped_not_refused():
    decision = boundary(max_proposal_notional=500.0).evaluate(proposal())
    assert decision.approved
    assert decision.approved_notional == 500.0
    assert decision.was_reduced
    assert "capped" in decision.reductions[0]


def test_the_boundary_never_enlarges_a_request():
    """The composition rule: agent output is an upper bound."""
    for requested in (1.0, 100.0, 10_000.0, 1e9):
        decision = boundary(max_proposal_notional=1e12).evaluate(
            proposal(desired_notional=requested)
        )
        assert decision.approved_notional <= requested, (
            f"the boundary turned {requested} into {decision.approved_notional}"
        )


def test_the_risk_engine_can_zero_a_proposal_and_the_agent_cannot_argue():
    def refuse(_instrument, _notional):
        return False, "exposure limits already breached"

    decision = boundary(risk_check=refuse).evaluate(proposal())
    assert not decision.approved
    assert decision.approved_notional == 0.0
    assert "risk engine refused" in decision.reasons[-1]


def test_a_low_confidence_proposal_is_refused():
    decision = boundary(min_confidence=0.9).evaluate(proposal(confidence=0.5))
    assert not decision.approved


def test_an_unsupported_instrument_is_refused():
    decision = boundary(
        allowed_instruments=frozenset({"AAPL", "SPY"})
    ).evaluate(proposal(instrument="DOGE/USD"))
    assert not decision.approved
    assert "not in the allowed instrument set" in decision.reasons[0]


def test_an_expired_proposal_is_refused():
    decision = boundary().evaluate(
        proposal(expires_at=NOW - timedelta(seconds=1)), now=NOW
    )
    assert not decision.approved
    assert "expired" in decision.reasons[0]


def test_an_unpromoted_agent_is_refused():
    decision = boundary(
        promoted_agents=frozenset({"momentum:v2"})
    ).evaluate(proposal(agent_id="momentum", agent_version="v1"))
    assert not decision.approved
    assert "no promotion evidence" in decision.reasons[0]


# ---------------------------------------------------------------------------
# Eligibility gates how far a proposal travels
# ---------------------------------------------------------------------------

def test_a_shadow_proposal_cannot_execute_live():
    decision = boundary(mode=Eligibility.LIVE).evaluate(
        proposal(eligibility=Eligibility.SHADOW)
    )
    assert not decision.approved
    assert "may observe, not execute" in decision.reasons[0]


def test_a_paper_proposal_cannot_execute_live():
    decision = boundary(mode=Eligibility.LIVE).evaluate(
        proposal(eligibility=Eligibility.PAPER)
    )
    assert not decision.approved


def test_a_live_eligible_proposal_runs_in_paper():
    """Eligibility is a ceiling, not an exact match."""
    decision = boundary(mode=Eligibility.PAPER).evaluate(
        proposal(eligibility=Eligibility.LIVE)
    )
    assert decision.approved


# ---------------------------------------------------------------------------
# Flatten and target exposure
# ---------------------------------------------------------------------------

def test_flatten_resolves_against_current_exposure():
    decision = boundary().evaluate(
        proposal(direction=Direction.FLATTEN, target_exposure=0.0),
        current_exposure=750.0,
    )
    assert decision.approved
    assert decision.approved_notional == 750.0


def test_flattening_a_flat_book_changes_nothing():
    decision = boundary().evaluate(
        proposal(direction=Direction.FLATTEN, target_exposure=0.0),
        current_exposure=0.0,
    )
    assert not decision.approved
    assert "no change in exposure" in decision.reasons[0]


def test_a_target_exposure_already_met_changes_nothing():
    decision = boundary().evaluate(
        proposal(desired_notional=None, target_exposure=1000.0),
        current_exposure=1000.0,
    )
    assert not decision.approved


# ---------------------------------------------------------------------------
# Agents cannot double-order
# ---------------------------------------------------------------------------

def test_two_agents_proposing_the_same_trade_produce_one():
    resolved = arbitrate([
        proposal(agent_id="a", confidence=0.6),
        proposal(agent_id="b", confidence=0.9),
    ])
    assert len(resolved) == 1
    assert resolved[0].agent_id == "b", "the more confident proposal wins"


def test_agents_proposing_opposite_trades_produce_none():
    """A disagreement is not two positions."""
    resolved = arbitrate([
        proposal(agent_id="a", direction=Direction.BUY),
        proposal(agent_id="b", direction=Direction.SELL),
    ])
    assert resolved == []


def test_arbitration_is_per_instrument():
    resolved = arbitrate([
        proposal(instrument="AAPL", agent_id="a"),
        proposal(instrument="SPY", agent_id="b"),
    ])
    assert {p.instrument for p in resolved} == {"AAPL", "SPY"}


def test_arbitration_is_deterministic_under_ties():
    first = proposal(agent_id="a", confidence=0.8, proposal_id="p-1",
                     created_at=NOW)
    second = proposal(agent_id="b", confidence=0.8, proposal_id="p-2",
                      created_at=NOW)
    assert arbitrate([first, second])[0].proposal_id == "p-1"
    assert arbitrate([second, first])[0].proposal_id == "p-1"


# ---------------------------------------------------------------------------
# The architectural guarantee
# ---------------------------------------------------------------------------

#: Methods that put real exposure on. Calling one outside the hardened
#: execution boundary skips the WAL, the lifecycle machine, idempotency, the
#: risk engine, the runtime state machine and live authorization.
BROKER_ORDER_METHODS = frozenset({
    "place_order",
    "place_market_order",
    "place_limit_order",
    "submit_order",
    "create_order",
    "close_position",
})

#: The modules permitted to speak to a broker. Everything else must route
#: through ExecutionEngine.
EXECUTION_BOUNDARY = {
    "core/execution.py",        # the boundary itself
    "core/alpaca_connector.py",  # the broker client it drives
    "core/exchange_connectors.py",
    "core/order_intent.py",
}

#: Call sites that are *on* an ExecutionEngine rather than a broker client.
#: These are the sanctioned path, not a bypass.
ENGINE_ATTRIBUTES = frozenset({
    "execution_engine", "_execution_engine", "executor", "_executor",
    "engine", "_engine", "execution",
})


def _python_sources():
    for path in sorted(ALPHA_IO.rglob("*.py")):
        relative = path.relative_to(ALPHA_IO).as_posix()
        if relative.startswith("tests/") or "__pycache__" in relative:
            continue
        if relative in EXECUTION_BOUNDARY:
            continue
        yield relative, path


def _direct_broker_calls(relative, path):
    """Calls to a broker order method on something that is not the engine."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in BROKER_ORDER_METHODS:
            continue
        # Identify the receiver. self.execution_engine.submit_order(...) is
        # the sanctioned path; self.alpaca_client.place_order(...) is not.
        receiver = func.value
        name = None
        if isinstance(receiver, ast.Attribute):
            name = receiver.attr
        elif isinstance(receiver, ast.Name):
            name = receiver.id
        if name in ENGINE_ATTRIBUTES:
            continue
        found.append(f"{relative}:{node.lineno}: {name}.{func.attr}()")
    return found


def test_no_module_outside_the_execution_boundary_calls_a_broker_directly():
    """A boundary is only a boundary if there is no way around it.

    This is the ULTRAPLAN's required static search, run as a test so a new
    bypass fails CI rather than being discovered in production.
    """
    offenders = []
    for relative, path in _python_sources():
        offenders.extend(_direct_broker_calls(relative, path))

    assert not offenders, (
        "These call a broker order method directly, bypassing the WAL, the "
        "order lifecycle, idempotency, the risk engine and live "
        "authorization:\n  " + "\n  ".join(offenders)
    )


def test_the_architectural_check_can_actually_detect_a_bypass(tmp_path):
    """Guard the guard: a scanner that finds nothing must be able to find something."""
    offender = tmp_path / "rogue.py"
    offender.write_text(
        "class Agent:\n"
        "    def go(self):\n"
        "        self.alpaca_client.place_market_order('AAPL', 1, 'buy')\n",
        encoding="utf-8",
    )
    found = _direct_broker_calls("rogue.py", offender)
    assert found, "the scanner cannot see a direct broker call"
    assert "place_market_order" in found[0]


def test_the_architectural_check_does_not_flag_the_sanctioned_path(tmp_path):
    allowed = tmp_path / "fine.py"
    allowed.write_text(
        "class Strategy:\n"
        "    def go(self):\n"
        "        order = self.execution_engine.create_order('AAPL', 1)\n"
        "        self.execution_engine.submit_order(order)\n",
        encoding="utf-8",
    )
    assert _direct_broker_calls("fine.py", allowed) == []
