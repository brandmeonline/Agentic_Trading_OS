# recursive_training.py – agent auto-training from signal memory + trade log

import pandas as pd
from core.agent import TradingAgent
from core.signal_memory import SignalMemory

def recursive_train(agent: TradingAgent, memory: SignalMemory, log_path="data/trade_log.csv"):
    try:
        df = pd.read_csv(log_path)
        for _, row in df.iterrows():
            context = f"{row['asset']} trade with confidence {row['confidence']} had P&L {row['pnl']}"
            memory.add_signal(context, row.to_dict())
            agent.update(row["confidence"], row["reward"])
        print("[RECURSIVE] Agent trained on historical trades.")
        return agent, memory
    except FileNotFoundError:
        print("[RECURSIVE] No trade log found.")
        return agent, memory

if __name__ == "__main__":
    agent = TradingAgent()
    memory = SignalMemory()
    recursive_train(agent, memory)