# multi_agent_fusion.py – Arbitration between specialized agent models

from core.agent import TradingAgent

class AgentSwarm:
    def __init__(self):
        self.agents = {
            "crypto": TradingAgent(),
            "macro": TradingAgent(),
            "equities": TradingAgent()
        }
        self.history = []

    def update_agents(self, domain, confidence, reward):
        if domain in self.agents:
            self.agents[domain].update(confidence, reward)

    def vote(self, signal_meta):
        votes = []
        for domain, agent in self.agents.items():
            conf = signal_meta.get(domain, 0.7)
            if agent.decide(conf):
                votes.append(domain)
        return {
            "votes": votes,
            "execute": len(votes) >= 2,  # majority rule
            "agents_in_agreement": votes
        }

    def summary(self):
        return {domain: agent.summary() for domain, agent in self.agents.items()}

# Example usage
if __name__ == "__main__":
    swarm = AgentSwarm()
    signal = {"crypto": 0.8, "macro": 0.75, "equities": 0.65}
    decision = swarm.vote(signal)
    print("[SWARM] Vote Result:", decision)
    print("[SWARM] Agent Summary:", swarm.summary())