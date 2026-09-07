"""ATOS-P2-DEPLOY-001 — liveness is not readiness, and images carry no secrets.

The failure this guards against is a deployment that looks fine. The container
had one probe, ``GET /api/v1/health``, returning ``{"status": "ok"}`` from a
lambda; both Docker's HEALTHCHECK and compose's healthcheck pointed at it. A
process that had lost its broker connection, failed reconciliation, or come
back from a crash with unresolved order intents answered it cheerfully.

Three families:

* readiness evaluation, which must be fail-closed in every direction;
* the probes as actually served, including the status codes a process manager
  reads;
* the deployment artifacts, checked against the repository's real Dockerfiles
  and compose files rather than against fixtures.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.readiness import (  # noqa: E402
    FORBIDDEN_RESTART_STATES,
    NO_EVIDENCE,
    READINESS_REQUIREMENTS,
    REQUIREMENT_KEYS,
    ReadinessReport,
    UnsafeRestart,
    check_restart_state,
    compose_problems,
    dockerfile_problems,
    evaluate_readiness,
    liveness,
)
from core.runtime_state import RuntimeState  # noqa: E402
from web.app import (  # noqa: E402
    WebConfig,
    create_app,
    make_admin_password_hash,
    trading_state,
)

pytestmark = pytest.mark.adversarial

REPO = Path(__file__).resolve().parents[2]
ALPHA = Path(__file__).resolve().parents[1]


def _all_satisfied():
    return {key: True for key in REQUIREMENT_KEYS}


# ---------------------------------------------------------------------------
# Readiness evaluation
# ---------------------------------------------------------------------------


def test_the_ultraplan_requirements_are_all_present():
    """Eleven, named by section 20. A dropped one is a silent hole."""
    expected = {
        "persistence_healthy",
        "broker_auth_healthy",
        "expected_account_confirmed",
        "reconciliation_fresh_and_matched",
        "data_healthy",
        "no_unresolved_order_intents",
        "no_risk_trip",
        "capital_tier_valid",
        "strategy_promotion_valid",
        "event_loops_healthy",
        "execution_adapter_healthy",
    }
    assert set(REQUIREMENT_KEYS) == expected
    assert len(READINESS_REQUIREMENTS) == len(expected)


def test_no_evidence_at_all_is_not_ready():
    """The property the whole module exists for."""
    report = evaluate_readiness({})

    assert report.ready is False
    assert report.http_status == 503
    assert len(report.blocking()) == len(REQUIREMENT_KEYS)
    assert all(o.detail == NO_EVIDENCE for o in report.blocking())


def test_a_single_missing_requirement_blocks_readiness():
    """Iterating the requirements, not the evidence, is what makes this work."""
    for key in REQUIREMENT_KEYS:
        evidence = _all_satisfied()
        del evidence[key]
        report = evaluate_readiness(evidence)
        assert report.ready is False, key
        assert [o.key for o in report.blocking()] == [key]


def test_a_single_failing_requirement_blocks_readiness():
    for key in REQUIREMENT_KEYS:
        evidence = _all_satisfied()
        evidence[key] = (False, "broken")
        report = evaluate_readiness(evidence)
        assert report.ready is False, key
        assert key in report.summary()


def test_full_evidence_is_ready():
    """Otherwise every assertion above passes for the wrong reason."""
    report = evaluate_readiness(_all_satisfied())
    assert report.ready is True
    assert report.http_status == 200
    assert report.blocking() == []
    assert report.summary() == "ready"


def test_a_check_that_raises_is_not_ready():
    def explode():
        raise RuntimeError("broker unreachable")

    evidence = _all_satisfied()
    evidence["broker_auth_healthy"] = explode
    report = evaluate_readiness(evidence)

    assert report.ready is False
    assert "RuntimeError" in report.summary()


def test_evidence_that_is_not_a_result_does_not_count():
    """A truthy string is the shape a half-finished check returns."""
    for junk in ("yes", 1, [], {"ok": True}, None):
        evidence = _all_satisfied()
        evidence["data_healthy"] = junk
        report = evaluate_readiness(evidence)
        assert report.ready is False, junk


def test_a_callable_returning_a_pair_is_read_correctly():
    evidence = _all_satisfied()
    evidence["persistence_healthy"] = lambda: (True, "wal fsynced")
    report = evaluate_readiness(evidence)
    assert report.ready is True
    detail = [o for o in report.outcomes if o.key == "persistence_healthy"][0]
    assert detail.detail == "wal fsynced"


def test_a_typo_in_an_evidence_key_still_blocks(caplog):
    """The dangerous case: the real requirement silently gets no evidence."""
    evidence = _all_satisfied()
    evidence["persistance_healthy"] = evidence.pop("persistence_healthy")

    report = evaluate_readiness(evidence)

    assert report.ready is False
    assert "persistence_healthy" in report.summary()
    assert "persistance_healthy" in caplog.text


def test_the_public_form_reports_no_internal_state():
    """An unauthenticated probe must not describe the system to a stranger."""
    report = evaluate_readiness({})
    public = report.public_dict()

    assert set(public) == {"ready", "blocking_count"}
    rendered = str(public)
    for key in REQUIREMENT_KEYS:
        assert key not in rendered
    assert NO_EVIDENCE not in rendered


def test_the_authenticated_form_does_report_the_reasons():
    detail = evaluate_readiness({}).to_dict()
    assert len(detail["requirements"]) == len(REQUIREMENT_KEYS)
    assert set(detail["blocking"]) == set(REQUIREMENT_KEYS)


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


def test_liveness_says_nothing_about_readiness():
    """Wiring readiness to a restart policy makes an unsafe system worse."""
    result = liveness()

    assert result["alive"] is True
    assert "ready" not in result
    assert set(result) & set(REQUIREMENT_KEYS) == set()
    assert "says nothing about whether the system may trade" in result["note"]


def test_liveness_reports_uptime_when_it_knows_it():
    now = datetime(2026, 4, 1, tzinfo=timezone.utc)
    result = liveness(now - timedelta(seconds=90), now=now)
    assert result["uptime_seconds"] == 90.0

    assert liveness(None, now=now)["uptime_seconds"] is None


def test_liveness_tolerates_a_naive_start_time():
    now = datetime(2026, 4, 1, tzinfo=timezone.utc)
    result = liveness(now.replace(tzinfo=None) - timedelta(seconds=5), now=now)
    assert result["uptime_seconds"] == 5.0


# ---------------------------------------------------------------------------
# Restart entry
# ---------------------------------------------------------------------------


def test_a_restart_may_not_come_back_into_a_trading_state():
    for state in FORBIDDEN_RESTART_STATES:
        with pytest.raises(UnsafeRestart):
            check_restart_state(state)


def test_a_restart_into_reconciling_or_a_non_live_state_is_permitted():
    for state in ("live_reconciling", "paper", "research", "recovery_required"):
        check_restart_state(state)  # must not raise


def test_an_unreviewed_state_is_refused_rather_than_waved_through():
    with pytest.raises(UnsafeRestart):
        check_restart_state("live_super_active")


def test_every_runtime_state_is_classified_one_way_or_the_other():
    """A new state added to the machine must not be silently permitted."""
    from core.readiness import PERMITTED_RESTART_STATES

    for state in RuntimeState:
        assert state.value in (PERMITTED_RESTART_STATES | FORBIDDEN_RESTART_STATES), state


def test_a_fresh_orchestrator_does_not_start_in_a_trading_state():
    """The concrete version of the rule, against the real orchestrator."""
    from core.orchestrator import TradingOrchestrator, OrchestratorConfig, TradingMode

    for mode in (TradingMode.PAPER, TradingMode.LIVE, TradingMode.BACKTEST):
        orchestrator = TradingOrchestrator(OrchestratorConfig(mode=mode))
        check_restart_state(orchestrator.runtime.state.value)
        assert orchestrator.runtime.state is not RuntimeState.LIVE_ACTIVE


def test_live_active_is_only_reachable_through_reconciling():
    """Pinned here as well as in the state machine: it is a deployment rule."""
    from core.runtime_state import RuntimeStateMachine

    for state in RuntimeState:
        machine = RuntimeStateMachine(state)
        if machine.can_transition_to(RuntimeState.LIVE_ACTIVE):
            assert state is RuntimeState.LIVE_RECONCILING, state


# ---------------------------------------------------------------------------
# The orchestrator's own readiness
# ---------------------------------------------------------------------------


def test_an_uninitialised_orchestrator_is_not_ready():
    from core.orchestrator import TradingOrchestrator

    report = TradingOrchestrator().readiness()

    assert isinstance(report, ReadinessReport)
    assert report.ready is False
    assert report.http_status == 503
    # Every requirement should have produced a real reason, not NO_EVIDENCE:
    # the orchestrator supplies a check for each one.
    assert all(o.detail != NO_EVIDENCE for o in report.outcomes)


def test_the_orchestrator_supplies_evidence_for_every_requirement():
    from core.orchestrator import TradingOrchestrator

    evidence = TradingOrchestrator().readiness_evidence()
    assert set(evidence) == set(REQUIREMENT_KEYS)


def test_a_dead_event_bus_is_not_alive():
    """A bus whose processor died still accepts publish() without complaint."""
    from core.orchestrator import EventBus

    bus = EventBus()
    assert bus.is_alive() is False

    bus.start()
    assert bus.is_alive() is True

    bus.stop()
    assert bus.is_alive() is False


def test_event_loops_are_not_healthy_while_the_system_is_stopped():
    from core.orchestrator import TradingOrchestrator

    orchestrator = TradingOrchestrator()
    satisfied, detail = orchestrator._check_event_loops()
    assert satisfied is False
    assert "not running" in detail


# ---------------------------------------------------------------------------
# The probes as served
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
    ))
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_state():
    saved = trading_state.__dict__.copy()
    yield
    trading_state.__dict__.clear()
    trading_state.__dict__.update(saved)


def test_the_liveness_probe_is_public_and_says_it_is_liveness(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["probe"] == "liveness"
    assert payload["alive"] is True
    assert "ready" not in payload


def test_the_readiness_probe_returns_503_when_not_ready(client):
    response = client.get("/api/ready")

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["ready"] is False
    assert payload["blocking_count"] > 0


def test_the_public_readiness_probe_leaks_nothing(client):
    body = client.get("/api/ready").data.decode()
    for key in REQUIREMENT_KEYS:
        assert key not in body


def test_the_detailed_readiness_route_requires_a_session(client):
    response = client.get("/api/readiness")
    assert response.status_code in (301, 302)
    assert "/login" in response.headers.get("Location", "")


def test_the_detailed_readiness_route_reports_every_requirement(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True

    response = client.get("/api/readiness")

    assert response.status_code == 503
    payload = response.get_json()
    assert {r["requirement"] for r in payload["requirements"]} == set(REQUIREMENT_KEYS)


def test_liveness_stays_200_while_readiness_is_503(client):
    """The distinction, in one test. A wedged-but-unsafe system must not be
    restarted by a health check that conflated the two."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/ready").status_code == 503


def test_the_rest_api_serves_both_probes_without_auth():
    from core.rest_api import RESTAPIServer, APIRequest

    server = RESTAPIServer()

    health, _ = server.router.find_route("/api/v1/health", "GET")
    ready, _ = server.router.find_route("/api/v1/ready", "GET")
    assert health is not None and ready is not None
    # A process manager cannot log in, so both probes must be reachable
    # without credentials.
    assert health.auth_required is False
    assert ready.auth_required is False

    response = server.get_readiness(
        _probe_request(APIRequest), {}
    )
    # No trading system attached, so it must refuse rather than assume.
    assert response.status_code == 503
    assert response.body["ready"] is False


def test_the_rest_api_readiness_reflects_an_attached_system():
    from core.rest_api import RESTAPIServer, APIRequest
    from core.orchestrator import TradingOrchestrator

    server = RESTAPIServer(trading_system=TradingOrchestrator())
    response = server.get_readiness(
        _probe_request(APIRequest), {}
    )
    assert response.status_code == 503
    assert response.body["probe"] == "readiness"

    class _Ready:
        def readiness(self):
            return evaluate_readiness(_all_satisfied())

    server = RESTAPIServer(trading_system=_Ready())
    response = server.get_readiness(
        _probe_request(APIRequest), {}
    )
    assert response.status_code == 200
    assert response.body["ready"] is True


def test_a_readiness_evaluation_that_raises_reports_not_ready():
    from core.rest_api import RESTAPIServer, APIRequest

    class _Broken:
        def readiness(self):
            raise RuntimeError("nope")

    server = RESTAPIServer(trading_system=_Broken())
    response = server.get_readiness(
        _probe_request(APIRequest), {}
    )
    assert response.status_code == 503
    assert response.body["ready"] is False


# ---------------------------------------------------------------------------
# Deployment artifacts
# ---------------------------------------------------------------------------


def _probe_request(APIRequest):
    """A bare GET, in whatever shape this API's request object wants."""
    return APIRequest(
        method="GET", path="/api/v1/ready",
        headers={}, query_params={}, body=None,
    )


def _dockerignore_for(dockerfile: Path) -> str:
    candidate = dockerfile.parent / ".dockerignore"
    return candidate.read_text(encoding="utf-8") if candidate.exists() else ""


def test_every_dockerfile_in_the_repository_is_clean():
    dockerfiles = [REPO / "Dockerfile", ALPHA / "Dockerfile"]
    assert all(d.exists() for d in dockerfiles), dockerfiles

    for dockerfile in dockerfiles:
        problems = dockerfile_problems(
            dockerfile.read_text(encoding="utf-8"), _dockerignore_for(dockerfile)
        )
        assert problems == [], f"{dockerfile}: {problems}"


def test_every_compose_file_in_the_repository_is_clean():
    composes = [REPO / "docker-compose.yml", ALPHA / "docker-compose.yml"]
    assert all(c.exists() for c in composes), composes

    for compose in composes:
        problems = compose_problems(compose.read_text(encoding="utf-8"))
        assert problems == [], f"{compose}: {problems}"


def test_the_auditor_catches_a_baked_credential_copy():
    """Otherwise the two tests above pass because the auditor is blind."""
    problems = dockerfile_problems(
        "FROM python:3.11\nCOPY .credentials /app/.credentials\n",
        dockerignore="",
    )
    assert any(".credentials" in p for p in problems)


def test_the_auditor_catches_a_secret_baked_into_an_env_line():
    problems = dockerfile_problems(
        "FROM python:3.11\nENV ALPACA_API_SECRET=abcd1234efgh5678\n",
        dockerignore="\n".join(
            [".credentials", ".env", "config", "secrets", "*.pem", "*.key"]
        ),
    )
    assert problems == [
        "line 2: ALPACA_API_SECRET is given a value in the image"
    ]


def test_the_auditor_permits_a_placeholder_or_an_injected_value():
    ignore = "\n".join([".credentials", ".env", "config", "secrets", "*.pem", "*.key"])
    for line in (
        "ENV ALPACA_API_SECRET=",
        "ENV ALPACA_API_SECRET=${ALPACA_API_SECRET}",
        "ARG DB_PASSWORD=changeme",
    ):
        assert dockerfile_problems(f"FROM python:3.11\n{line}\n", ignore) == [], line


def test_the_auditor_catches_a_compose_default_secret():
    problems = compose_problems("      - DB_PASSWORD=${DB_PASSWORD:-trading123}\n")
    assert problems and "baked-in default" in problems[0]

    assert compose_problems("      - DB_PASSWORD=${DB_PASSWORD:?set it}\n") == []
    # An empty default supplies nothing, so it bakes nothing.
    assert compose_problems("      - BINANCE_API_SECRET=${BINANCE_API_SECRET:-}\n") == []


def test_the_auditor_catches_a_literal_secret_in_compose():
    problems = compose_problems("      - POSTGRES_PASSWORD=hunter2\n")
    assert problems and "literal value" in problems[0]


def test_the_dockerignore_excludes_every_credential_path():
    from core.readiness import SECRET_BEARING_PATHS

    for root in (REPO, ALPHA):
        ignore = (root / ".dockerignore").read_text(encoding="utf-8")
        entries = {
            line.strip().rstrip("/") for line in ignore.splitlines()
            if line.strip() and not line.startswith("#")
        }
        for path in SECRET_BEARING_PATHS:
            assert path.rstrip("/") in entries, f"{root}: {path}"


def test_the_container_health_check_is_wired_to_liveness_not_readiness():
    """Docker's HEALTHCHECK drives restarts. Pointing it at readiness would
    restart a system precisely when a restart makes things worse."""
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    healthcheck = [
        line for line in text.splitlines()
        if "urlopen" in line and "localhost" in line
    ]
    assert healthcheck
    for line in healthcheck:
        assert "/api/v1/health" in line
        assert "/ready" not in line

    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    for line in compose.splitlines():
        if "healthcheck" in line.lower() or "urlopen" in line:
            assert "/api/v1/ready" not in line
