# intent_graph.py – visualize the flow of decisions from signals to agents to outcomes

import networkx as nx
import matplotlib.pyplot as plt
import pandas as pd

def build_intent_graph(log_path="data/trade_log.csv", save_path="dashboard/intent_graph.png"):
    try:
        df = pd.read_csv(log_path)
        df = df.tail(20)  # use last 20 trades

        G = nx.DiGraph()

        for i, row in df.iterrows():
            signal_node = f"Signal_{i}_{row['asset']}"
            agent_node = f"Agent_{row['asset']}"
            pnl_node = f"{'Profit' if row['pnl'] > 0 else 'Loss'}_{abs(int(row['pnl']))}"

            G.add_node(signal_node, type='signal')
            G.add_node(agent_node, type='agent')
            G.add_node(pnl_node, type='result')

            G.add_edge(signal_node, agent_node, weight=row['confidence'])
            G.add_edge(agent_node, pnl_node, weight=row['pnl'])

        plt.figure(figsize=(14, 10))
        pos = nx.spring_layout(G, seed=42)
        node_colors = ['skyblue' if G.nodes[n]['type'] == 'signal' else 'orange' if G.nodes[n]['type'] == 'agent' else 'lightgreen' for n in G.nodes]
        nx.draw(G, pos, with_labels=True, node_size=1500, node_color=node_colors, font_size=10, edge_color='gray')
        plt.title("Trading Intent Graph – Signals to Outcomes", fontsize=16)
        plt.savefig(save_path)
        print(f"[INTENT GRAPH] Saved to {save_path}")
    except Exception as e:
        print(f"[INTENT GRAPH ERROR] {e}")

if __name__ == "__main__":
    build_intent_graph()