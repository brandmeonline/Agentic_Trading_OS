# signal_memory.py – long-term vectorized memory of alpha signals

import numpy as np

from core.llm_client import DEFAULT_EMBED_DIM, DEFAULT_EMBED_MODEL, embed


class SignalMemory:
    """FAISS-backed store of past signals, searchable by semantic similarity.

    ``faiss`` is imported lazily so that importing this module — which
    ``core.multi_agent_fusion_memory`` does at import time — does not require the
    optional dependency to be installed.
    """

    def __init__(self, dim=DEFAULT_EMBED_DIM, model=DEFAULT_EMBED_MODEL):
        self.dim = dim
        self.model = model
        self.index = self._new_index(dim)
        self.metadata = []

    @staticmethod
    def _new_index(dim):
        try:
            import faiss
        except ImportError as exc:
            raise ImportError(
                "faiss is required for SignalMemory. Install it with: pip install faiss-cpu"
            ) from exc
        return faiss.IndexFlatL2(dim)

    def embed(self, text):
        """Embed text to a float32 vector.

        Raises ``core.llm_client.LLMUnavailable`` when no key or SDK is present,
        rather than failing inside a removed SDK symbol.
        """
        vector = np.array(embed(text, model=self.model), dtype="float32")
        if vector.shape[0] != self.dim:
            raise ValueError(
                f"embedding dimension {vector.shape[0]} does not match index dimension {self.dim}"
            )
        return vector

    def add_signal(self, text, metadata):
        vector = self.embed(text)
        self.index.add(np.array([vector]))
        self.metadata.append(metadata)

    def search_similar(self, query_text, k=5):
        if not self.metadata:
            return []
        query_vector = self.embed(query_text)
        _, indices = self.index.search(np.array([query_vector]), min(k, len(self.metadata)))
        return [self.metadata[i] for i in indices[0] if 0 <= i < len(self.metadata)]


# Example usage
if __name__ == "__main__":
    memory = SignalMemory()
    memory.add_signal("Bullish on ETH due to Shanghai upgrade", {"ticker": "ETH", "score": 0.85})
    memory.add_signal("CPI print looks bad, risk-off likely", {"macro": True, "score": 0.78})
    print(memory.search_similar("ETH will pump due to upgrade"))
