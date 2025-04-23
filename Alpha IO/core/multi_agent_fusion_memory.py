# multi_agent_fusion_memory.py – Swarm voting with memory-enhanced judgment

from core.agent import TradingAgent
from core.signal_memory import SignalMemory

class MemoryVotingSwarm:
    def __init__(self, memory=None):
        self.agents = {
            "crypto": TradingAgent(),
            "macro": TradingAgent(),
            "equities": TradingAgent()
        }
        self.memory = memory or SignalMemory()

    def vote_with_memory(self, signal_meta, query_text):
        votes = []
        similar_signals = self.memory.search_similar(query_text, k=3)
        memory_boost = any(float(s.get("score", 0)) > 0.7 for s in similar_signals)

        for domain, agent in self.agents.items():
            conf = signal_meta.get(domain, 0.7)
            conf += 0.05 if memory_boost else 0
            if agent.decide(conf):
                votes.append(domain)

        return {
            "votes": votes,
            "execute": len(votes) >= 2,
            "agents_in_agreement": votes,
            "memory_context": similar_signals
        }

if __name__ == "__main__":
    swarm = MemoryVotingSwarm()
    signal = {"crypto": 0.75, "macro": 0.70, "equities": 0.68}
    query = "Bullish breakout for ADA post Fed pause"
    decision = swarm.vote_with_memory(signal, query)
    print("[MEMORY-SWARM] Decision:", decision)