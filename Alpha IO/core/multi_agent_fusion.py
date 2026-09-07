"""Arbitration between specialized agent models — ATOS-P3-AGENT-001.

This is the swarm the README advertises. It used to end at ``vote()``, which
returned ``{"execute": len(votes) >= 2}`` - a majority of three agents built
from the same class, and a boolean called "execute".

Two things are wrong with that, and the second is the one that costs money:

* Three instances of ``TradingAgent`` agreeing is one agent counted three
  times. A majority among them is not evidence of anything.

* A key called "execute" invites a caller to act on it. Nothing between that
  dict and a broker checked identity, calibration, staleness, size, or
  whether a risk limit was tripped.

So ``vote()`` still exists and still reports what the agents thought, but it
no longer offers a verdict; it says so in the payload. Anything that wants a
trade calls ``propose()``, which produces validated
:class:`~core.swarm_arbitration.Vote` objects for the arbiter, where identity,
calibration, contradiction, deduplication, provenance and hard risk are all
somebody's explicit job.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.agent import TradingAgent
from core.swarm_arbitration import AgentIdentity, Vote
from core.trade_proposal import Direction

#: Bumped when an agent's behaviour changes. Calibration is tracked per
#: agent-version, so a retrained agent starts again with no track record -
#: which is the point.
AGENT_VERSION = "v1"


class AgentSwarm:
    def __init__(self, version: str = AGENT_VERSION):
        self.version = version
        self.agents = {
            "crypto": TradingAgent(),
            "macro": TradingAgent(),
            "equities": TradingAgent()
        }
        self.history: List[Dict[str, Any]] = []

    def identity(self, domain: str) -> AgentIdentity:
        return AgentIdentity(agent_id=domain, version=self.version,
                             domain=domain)

    def update_agents(self, domain, confidence, reward):
        if domain in self.agents:
            self.agents[domain].update(confidence, reward)

    def vote(self, signal_meta):
        """What each agent thought. Deliberately not a decision.

        The "execute" key this used to return is gone. It was a boolean
        derived from three correlated agents, sitting one attribute access
        away from a broker call, and nothing in between checked anything.
        """
        votes = []
        for domain, agent in self.agents.items():
            conf = signal_meta.get(domain, 0.7)
            if agent.decide(conf):
                votes.append(domain)
        return {
            "votes": sorted(votes),
            "agents_in_agreement": sorted(votes),
            "is_decision": False,
            "note": "agent opinions only; call propose() and arbitrate them",
        }

    def propose(
        self,
        instrument: str,
        signal_meta: Dict[str, float],
        horizon: timedelta = timedelta(hours=4),
        notional: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> List[Vote]:
        """One Vote per agent that wants to act, for the arbiter to resolve.

        Direction comes from the sign of the signal rather than from a
        separate field, so an agent cannot report a bullish confidence and a
        sell in the same breath.
        """
        cast_at = now or datetime.now(timezone.utc)
        proposals: List[Vote] = []
        for domain, agent in sorted(self.agents.items()):
            raw = signal_meta.get(domain)
            if raw is None:
                continue
            confidence = min(1.0, abs(float(raw)))
            if not agent.decide(confidence):
                continue
            proposals.append(Vote(
                agent=self.identity(domain),
                instrument=instrument,
                direction=Direction.BUY if raw >= 0 else Direction.SELL,
                confidence=confidence,
                horizon=horizon,
                desired_notional=notional,
                rationale=f"{domain} signal {raw:+.2f}",
                cast_at=cast_at,
            ))
        return proposals

    def summary(self):
        return {domain: agent.summary() for domain, agent in self.agents.items()}
