# signal_augment.py – generate synthetic alpha signals based on past winners

import pandas as pd
import openai
from core.signal_memory import SignalMemory
from sklearn.cluster import KMeans
import numpy as np
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

class SignalAugmentor:
    def __init__(self, memory: SignalMemory):
        self.memory = memory

    def cluster_successful_signals(self, trade_log_path, num_clusters=3):
        df = pd.read_csv(trade_log_path)
        winners = df[df["pnl"] > 0]
        texts = [
            f"Asset: {row['asset']}, Confidence: {row['confidence']}, PnL: {row['pnl']}"
            for _, row in winners.iterrows()
        ]
        vectors = np.array([self.memory.embed(text) for text in texts])
        kmeans = KMeans(n_clusters=num_clusters, random_state=0).fit(vectors)
        clusters = {i: [] for i in range(num_clusters)}
        for idx, label in enumerate(kmeans.labels_):
            clusters[label].append(texts[idx])
        return clusters

    def synthesize_from_clusters(self, clusters, examples_per_cluster=2):
        synthetic_signals = []
        for label, examples in clusters.items():
            prompt = (
                "Generate new trading alpha signals inspired by these successful examples:
" +
                "
".join(examples[:5]) +
                "

New signals:"
            )
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a trading signal generator."},
                    {"role": "user", "content": prompt}
                ]
            )
            synthetic_signals.append({
                "cluster": label,
                "signals": response["choices"][0]["message"]["content"]
            })
        return synthetic_signals

if __name__ == "__main__":
    memory = SignalMemory()
    augmentor = SignalAugmentor(memory)
    clusters = augmentor.cluster_successful_signals("data/trade_log.csv")
    new_signals = augmentor.synthesize_from_clusters(clusters)
    for block in new_signals:
        print(f"[AUGMENTED SIGNALS] Cluster {block['cluster']}:
{block['signals']}")