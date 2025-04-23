# signal_memory.py – long-term vectorized memory of alpha signals

import faiss
import numpy as np
import openai
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

class SignalMemory:
    def __init__(self, dim=1536):
        self.dim = dim
        self.index = faiss.IndexFlatL2(dim)
        self.metadata = []

    def embed(self, text):
        response = openai.Embedding.create(
            input=text,
            model="text-embedding-3-small"
        )
        return np.array(response["data"][0]["embedding"], dtype="float32")

    def add_signal(self, text, metadata):
        vector = self.embed(text)
        self.index.add(np.array([vector]))
        self.metadata.append(metadata)

    def search_similar(self, query_text, k=5):
        query_vector = self.embed(query_text)
        D, I = self.index.search(np.array([query_vector]), k)
        return [self.metadata[i] for i in I[0] if i < len(self.metadata)]

# Example usage
if __name__ == "__main__":
    memory = SignalMemory()
    memory.add_signal("Bullish on ETH due to Shanghai upgrade", {"ticker": "ETH", "score": 0.85})
    memory.add_signal("CPI print looks bad, risk-off likely", {"macro": True, "score": 0.78})
    print(memory.search_similar("ETH will pump due to upgrade"))