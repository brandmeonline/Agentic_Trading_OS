"""ATOS-P2-UI-001 — the dashboard never renders demo data as healthy live state.

These tests exist because the failure they guard against is silent. Nothing
crashes when a dashboard shows a fabricated DeFi balance next to a real
account balance; it just quietly becomes a screen an operator believes.

Three families here:

* the status vocabulary itself — pessimistic defaults, freshness that treats
  "unknown" as bad news, and a severity ordering that puts DEMO above
  UNAVAILABLE;
* the assembled bar — the conditions the ULTRAPLAN requires it to report;
* the rendered surface — every authenticated page carries the bar, and the
  endpoints backed by generated data say so in their payload.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.operator_status import (  # noqa: E402
    BLOCKED_MODES,
    DEFAULT_DATA_FRESHNESS,
    UNTRADEABLE_TRUST,
    BrokerKind,
    DataTrust,
    ExecutionState,
    OperatingMode,
    OperatorStatus,
    ReconciliationState,
    Surface,
    account_fingerprint,
    build_operator_status,
    classify_freshness,
    dangerous_controls,
    mode_from_runtime,
    read_trust,
    reconciliation_from_report,
    separation_problems,
    stamp,
    worst_trust,
)
from core.runtime_state import RuntimeState  # noqa: E402
from web.app import (  # noqa: E402
    DEMO_BACKED_SURFACES,
    TradingState,
    WebConfig,
    create_app,
    make_admin_password_hash,
    trading_state,
)

pytestmark = pytest.mark.adversarial

WEB = Path(__file__).resolve().parents[1] / "web"
NOW = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_a_freshly_constructed_status_is_not_fit_to_trade():
    """The whole point. An unwired dashboard must not show a green bar."""
    status = OperatorStatus()

    assert status.fit_to_trade is False
    assert status.mode is OperatingMode.UNKNOWN
    assert status.broker is BrokerKind.UNKNOWN
    assert status.execution is ExecutionState.DISABLED
    assert status.reconciliation is ReconciliationState.UNKNOWN
    assert status.data_trust is DataTrust.UNAVAILABLE
    assert status.persistence_healthy is False
    assert status.banner()["level"] != "ok"


def test_every_default_badge_is_the_pessimistic_one():
    """Field by field, so a future default cannot quietly become optimistic."""
    status = OperatorStatus()
    rendered = status.to_dict()

    assert rendered["mode"] == "UNKNOWN"
    assert rendered["broker"] == "UNKNOWN"
    assert rendered["broker_account"] == "unidentified"
    assert rendered["data"] == "UNAVAILABLE"
    assert rendered["execution"] == "DISABLED"
    assert rendered["reconciliation"] == "UNKNOWN"
    assert rendered["broker_connection_age_seconds"] is None
    assert rendered["market_data_age_seconds"] is None
    assert rendered["last_persistence_ok_at"] is None
    assert rendered["fit_to_trade"] is False


def test_worst_trust_ranks_demo_above_unavailable():
    """Fabricated plausibility beats visible absence as a hazard."""
    assert worst_trust([DataTrust.DEMO, DataTrust.UNAVAILABLE]) is DataTrust.DEMO
    assert worst_trust([DataTrust.LIVE, DataTrust.DEMO]) is DataTrust.DEMO
    assert worst_trust([DataTrust.LIVE, DataTrust.STALE]) is DataTrust.STALE
    assert worst_trust([DataTrust.STALE, DataTrust.INVALID]) is DataTrust.INVALID
    assert worst_trust([DataTrust.LIVE, DataTrust.LIVE]) is DataTrust.LIVE


def test_an_empty_set_of_declarations_is_unavailable_not_live():
    """Declaring nothing is not the same as declaring everything healthy."""
    assert worst_trust([]) is DataTrust.UNAVAILABLE


def test_freshness_treats_an_unknown_age_as_unavailable():
    assert classify_freshness(None, timedelta(seconds=30)) is DataTrust.UNAVAILABLE


def test_freshness_inside_and_outside_the_window():
    window = timedelta(seconds=30)
    assert classify_freshness(timedelta(seconds=1), window) is DataTrust.LIVE
    assert classify_freshness(window, window) is DataTrust.LIVE
    assert classify_freshness(timedelta(seconds=31), window) is DataTrust.STALE


def test_a_future_timestamp_is_invalid_not_maximally_fresh():
    """A clock problem must not read as the freshest possible observation."""
    result = classify_freshness(timedelta(seconds=-5), timedelta(seconds=30))
    assert result is DataTrust.INVALID


def test_the_account_fingerprint_is_never_the_account_id():
    account = "PA3X9Q71KLMN"
    fingerprint = account_fingerprint(account)

    assert account not in fingerprint
    assert fingerprint != account
    assert len(fingerprint) == 8
    # Stable across calls, so an operator can learn to recognise it.
    assert fingerprint == account_fingerprint(account)
    # And distinguishing, which is the reason it is on the bar at all.
    assert fingerprint != account_fingerprint("PA0000000000")


def test_an_absent_account_is_named_unidentified():
    assert account_fingerprint(None) == "unidentified"
    assert account_fingerprint("") == "unidentified"


def test_an_unstamped_payload_is_untrusted():
    """Forgetting the stamp must not be the comfortable path."""
    assert read_trust({"positions": []}) is DataTrust.UNAVAILABLE
    assert read_trust(None) is DataTrust.UNAVAILABLE
    assert read_trust([1, 2, 3]) is DataTrust.UNAVAILABLE


def test_a_stamped_payload_round_trips_and_a_corrupt_one_is_invalid():
    payload = stamp({"x": 1}, DataTrust.DEMO, "generated", source="defi")
    assert read_trust(payload) is DataTrust.DEMO
    assert payload["tradeable"] is False
    assert stamp({}, DataTrust.LIVE)["tradeable"] is True
    assert read_trust({"trust": "not-a-level"}) is DataTrust.INVALID


def test_every_runtime_state_maps_to_a_badge():
    """The map must cover the machine, or a live state renders as UNKNOWN."""
    for state in RuntimeState:
        assert mode_from_runtime(state.value) is not OperatingMode.UNKNOWN, state


def test_an_unrecognised_runtime_state_is_unknown_not_a_guess():
    assert mode_from_runtime("some_new_state") is OperatingMode.UNKNOWN
    assert mode_from_runtime(None) is OperatingMode.UNKNOWN


def test_live_reconciling_reads_as_live_armed():
    """Real money, not yet cleared to acquire — one badge for both."""
    assert mode_from_runtime("live_reconciling") is OperatingMode.LIVE_ARMED
    assert mode_from_runtime("live_active") is OperatingMode.LIVE
    assert mode_from_runtime("halted") in BLOCKED_MODES


def test_a_missing_reconciliation_report_is_unknown_not_matched():
    """The absence of a mismatch is not the presence of agreement."""
    assert reconciliation_from_report(None) is ReconciliationState.UNKNOWN

    class _Report:
        may_acquire = True

    class _Mismatched:
        may_acquire = False

    assert reconciliation_from_report(_Report()) is ReconciliationState.MATCHED
    assert reconciliation_from_report(_Mismatched()) is ReconciliationState.MISMATCH
    assert reconciliation_from_report(object()) is ReconciliationState.UNKNOWN


# ---------------------------------------------------------------------------
# The assembled bar
# ---------------------------------------------------------------------------


def _healthy(**overrides):
    """A status with nothing wrong, so each test can break exactly one thing."""
    defaults = dict(
        runtime_state="paper",
        broker=BrokerKind.PAPER,
        broker_account_id="PA123",
        execution_enabled=True,
        reconciliation=ReconciliationState.MATCHED,
        broker_seen_at=NOW - timedelta(seconds=2),
        market_data_at=NOW - timedelta(seconds=2),
        effective_capital_at_risk=100.0,
        capital_tier_limit=1000.0,
        last_persistence_ok_at=NOW - timedelta(seconds=10),
        surfaces=[Surface("prices", DataTrust.LIVE, "poll")],
        now=NOW,
    )
    defaults.update(overrides)
    return build_operator_status(**defaults)


def test_the_baseline_used_by_these_tests_is_actually_clean():
    """Otherwise every assertion below passes for the wrong reason."""
    status = _healthy()
    assert status.problems() == []
    assert status.fit_to_trade is True
    assert status.banner()["level"] == "ok"


def test_a_demo_panel_is_reported_even_when_everything_else_is_healthy():
    status = _healthy(surfaces=[
        Surface("prices", DataTrust.LIVE, "poll"),
        Surface("defi_portfolio", DataTrust.DEMO, "sample data"),
    ])

    assert status.fit_to_trade is False
    assert status.data_trust is DataTrust.DEMO
    assert any("demo data on screen" in p for p in status.problems())
    assert "defi_portfolio" in " ".join(status.problems())


def test_a_demo_panel_while_live_is_called_out_separately():
    """INV-ATOS-012 in one assertion."""
    status = _healthy(
        runtime_state="live_active",
        surfaces=[Surface("defi_portfolio", DataTrust.DEMO, "sample data")],
    )

    problems = status.problems()
    assert any("real capital is committed" in p for p in problems)
    assert status.banner()["level"] == "critical"


def test_stale_market_data_is_reported():
    status = _healthy(market_data_at=NOW - DEFAULT_DATA_FRESHNESS - timedelta(seconds=1))
    assert any("market data is stale" in p for p in status.problems())


def test_execution_enabled_on_untradeable_data_is_its_own_problem():
    for trust in sorted(UNTRADEABLE_TRUST, key=lambda t: t.value):
        status = _healthy(
            execution_enabled=True,
            surfaces=[Surface("prices", trust, "degraded")],
        )
        assert any("execution is enabled on" in p for p in status.problems()), trust


def test_a_capital_breach_is_reported_with_both_numbers():
    status = _healthy(effective_capital_at_risk=1500.0, capital_tier_limit=1000.0)

    assert status.capital_breached is True
    assert status.capital_headroom == -500.0
    breach = [p for p in status.problems() if "exceeds the tier limit" in p]
    assert breach and "1,500.00" in breach[0] and "1,000.00" in breach[0]
    assert status.banner()["level"] == "critical"


def test_live_with_no_configured_tier_limit_is_a_problem():
    status = _healthy(runtime_state="live_active", capital_tier_limit=None)
    assert any("no capital tier limit" in p for p in status.problems())


def test_unknown_orders_escalate_the_banner():
    status = _healthy(unknown_order_count=2)
    assert any("2 order(s) in an unknown state" in p for p in status.problems())
    assert status.banner()["level"] == "critical"


def test_a_reconciliation_mismatch_is_reported():
    status = _healthy(reconciliation=ReconciliationState.MISMATCH)
    assert any("reconciliation reports a mismatch" in p for p in status.problems())


def test_live_without_a_reconciliation_result_is_reported():
    status = _healthy(
        runtime_state="live_active", reconciliation=ReconciliationState.UNKNOWN
    )
    assert any("broker agreement is unestablished" in p for p in status.problems())


def test_active_risk_trips_are_named():
    status = _healthy(active_risk_trips=["daily_drawdown", "loss_streak"])
    named = [p for p in status.problems() if "active risk trip" in p]
    assert named and "daily_drawdown" in named[0] and "loss_streak" in named[0]


def test_persistence_that_was_never_confirmed_differs_from_one_gone_stale():
    never = _healthy(last_persistence_ok_at=None)
    assert any("never been confirmed" in p for p in never.problems())

    stale = _healthy(last_persistence_ok_at=NOW - timedelta(hours=2))
    assert any("no recent successful persistence" in p for p in stale.problems())
    assert stale.persistence_healthy is False


def test_a_disconnected_broker_is_unavailable_however_recent_the_heartbeat():
    """A torn-down connection is not made live by an old success."""
    status = _healthy(
        broker=BrokerKind.DISCONNECTED, broker_seen_at=NOW - timedelta(seconds=1)
    )
    assert status.broker_trust is DataTrust.UNAVAILABLE
    assert any("broker connection is unavailable" in p for p in status.problems())


def test_a_blocked_mode_is_stated_first():
    status = _healthy(runtime_state="frozen")
    assert status.problems()[0] == "system is FROZEN"
    assert status.banner()["level"] == "critical"
    assert "FROZEN" in status.banner()["text"]


def test_the_wire_form_carries_every_field_the_ultraplan_requires():
    rendered = _healthy().to_dict()
    required = {
        "mode", "broker", "broker_account", "data", "execution",
        "reconciliation", "broker_connection_age_seconds",
        "market_data_age_seconds", "effective_capital_at_risk",
        "capital_tier_limit", "reserved_capital", "open_order_count",
        "unknown_order_count", "active_risk_trips", "last_persistence_ok_at",
    }
    assert required <= set(rendered)


def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing():
    """Half this codebase still calls datetime.now(); the bar must survive it."""
    status = build_operator_status(
        runtime_state="paper",
        broker_seen_at=NOW.replace(tzinfo=None) - timedelta(seconds=5),
        now=NOW,
    )
    assert status.broker_connection_age == timedelta(seconds=5)


# ---------------------------------------------------------------------------
# Separation of dangerous controls from read-only analytics
# ---------------------------------------------------------------------------


def test_a_dangerous_control_on_an_analytics_page_is_a_problem():
    surfaces = [
        Surface("returns", DataTrust.LIVE, "ledger"),
        Surface("flatten_all", DataTrust.LIVE, "control", dangerous_control=True),
    ]
    problems = separation_problems(surfaces, page_is_analytics=True)
    assert problems and "flatten_all" in problems[0]


def test_a_dangerous_control_on_an_unauthenticated_page_is_a_problem():
    surfaces = [
        Surface("live_arm", DataTrust.LIVE, "control", dangerous_control=True),
    ]
    problems = separation_problems(
        surfaces, page_is_analytics=False, authenticated=False
    )
    assert problems and "unauthenticated" in problems[0]


def test_analytics_with_no_controls_is_clean():
    surfaces = [Surface("returns", DataTrust.LIVE, "ledger")]
    assert separation_problems(surfaces, page_is_analytics=True) == []
    assert dangerous_controls(surfaces) == []


# ---------------------------------------------------------------------------
# The rendered dashboard
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
    ))
    app.config["TESTING"] = True
    test_client = app.test_client()
    with test_client.session_transaction() as sess:
        sess["logged_in"] = True
    yield test_client


@pytest.fixture(autouse=True)
def clean_state():
    """The module-level trading_state is shared; do not leak between tests."""
    saved = trading_state.__dict__.copy()
    yield
    trading_state.__dict__.clear()
    trading_state.__dict__.update(saved)


def _page_endpoints(app):
    for rule in app.url_map.iter_rules():
        if rule.arguments or "GET" not in rule.methods:
            continue
        path = str(rule)
        if path.startswith("/api/") or path.startswith("/static"):
            continue
        if path in ("/login", "/logout"):
            continue
        yield path


def test_every_authenticated_page_renders_the_operator_bar(client):
    """The invariant is 'every page', so enumerate them rather than list them."""
    checked = []
    for path in _page_endpoints(client.application):
        response = client.get(path)
        if response.status_code != 200:
            continue
        body = response.data.decode("utf-8", errors="ignore")
        assert 'id="operator-bar"' in body, f"{path} has no operator status bar"
        assert "MODE " in body, f"{path} does not show the mode badge"
        checked.append(path)

    # If routing changed and nothing was reachable, the loop above would pass
    # vacuously. It must actually have covered the dashboard.
    assert "/" in checked
    assert len(checked) >= 5, checked


def test_the_bar_is_in_the_base_template_not_copied_into_each_page():
    """A per-page copy is a per-page omission waiting to happen."""
    base = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'id="operator-bar"' in base

    for template in (WEB / "templates").glob("*.html"):
        if template.name in ("base.html", "login.html"):
            continue
        text = template.read_text(encoding="utf-8")
        assert text.lstrip().startswith('{% extends "base.html" %}'), template.name
        assert 'id="operator-bar"' not in text, template.name


def test_the_status_endpoint_reports_the_required_fields(client):
    response = client.get("/api/operator-status")
    assert response.status_code == 200
    payload = response.get_json()

    for key in (
        "mode", "broker", "broker_account", "data", "execution",
        "reconciliation", "broker_connection_age_seconds",
        "market_data_age_seconds", "effective_capital_at_risk",
        "capital_tier_limit", "reserved_capital", "open_order_count",
        "unknown_order_count", "active_risk_trips", "last_persistence_ok_at",
        "problems", "banner",
    ):
        assert key in payload, key


def test_the_status_endpoint_requires_a_session():
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
    ))
    app.config["TESTING"] = True
    response = app.test_client().get("/api/operator-status")
    assert response.status_code in (301, 302)
    assert "/login" in response.headers.get("Location", "")


def test_an_unwired_dashboard_reports_itself_as_unfit(client):
    payload = client.get("/api/operator-status").get_json()

    assert payload["fit_to_trade"] is False
    assert payload["mode"] == "UNKNOWN"
    assert payload["execution"] == "DISABLED"
    assert payload["banner"]["level"] != "ok"


def test_the_defi_panels_are_declared_demo_by_the_dashboard(client):
    """core/blockchain.py generates its numbers; the bar must say so."""
    payload = client.get("/api/operator-status").get_json()
    names = {s["name"]: s["trust"] for s in payload["surfaces"]}

    for surface in DEMO_BACKED_SURFACES:
        assert names.get(surface) == "demo", surface
    assert payload["data"] == "DEMO"


def test_the_blockchain_endpoints_stamp_their_payloads(client):
    for path in ("/api/blockchain/portfolio", "/api/blockchain/chains",
                 "/api/blockchain/defi"):
        payload = client.get(path).get_json()
        if not payload.get("success"):
            continue  # the optional module is absent; nothing was rendered
        assert payload["trust"] == "demo", path
        assert payload["tradeable"] is False, path


def test_the_demo_surface_list_still_describes_the_code_it_claims_to():
    """If blockchain.py stops fabricating, this list should stop claiming it.

    The reverse is the case that matters: a new generated panel added without
    a declaration leaves the bar reporting LIVE for invented numbers.
    """
    source = (WEB.parent / "core" / "blockchain.py").read_text(encoding="utf-8")
    assert re.search(r"random\.(uniform|randint)", source), (
        "core/blockchain.py no longer generates data; revisit "
        "DEMO_BACKED_SURFACES in web/app.py"
    )


def test_a_broker_heartbeat_is_recorded_and_cleared():
    state = TradingState()
    assert state.last_broker_ok_at is None

    state.last_broker_ok_at = datetime.now(timezone.utc)
    state.stop_background_tasks(timeout=0.1)

    # A torn-down connection must stop looking recently healthy.
    assert state.last_broker_ok_at is None


def test_updating_a_price_records_when_it_was_observed():
    state = TradingState()
    assert state.last_price_at is None

    state.update_price("AAPL", 190.0)

    assert state.last_price_at is not None
    age = datetime.now(timezone.utc) - state.last_price_at
    assert age < timedelta(seconds=5)


def test_the_refresh_script_degrades_rather_than_freezing_the_last_good_bar():
    """A bar that keeps showing green after the server stops answering is the
    exact failure this issue is about, so the client path is pinned too."""
    script = (WEB / "static" / "js" / "operator-status.js").read_text(
        encoding="utf-8"
    )
    assert "degrade" in script
    assert "catch" in script
    assert "MODE UNKNOWN" in script


def test_the_defi_overview_endpoint_actually_returns_its_data(client):
    """It never did. Every request raised on a ChainType inside jsonify, and
    the page rendered the resulting error as an empty protocol list — an
    outage indistinguishable from "there is nothing here"."""
    payload = client.get("/api/blockchain/defi").get_json()

    assert payload["success"] is True, payload.get("error")
    assert payload["defi"]["total_protocols"] > 0
    chains = payload["defi"]["protocols_by_type"]["dex"][0]["chains"]
    assert chains and all(isinstance(chain, str) for chain in chains)
