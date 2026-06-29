# rag_macro.py – plug macro context into signal confidence scoring
import openai

def query_macro_context(prompt):
    # Placeholder using OpenAI - will be integrated with a macro source + vector DB.
    # When that RAG/vector-DB path lands, the retrieved chunks are the high-volume
    # surface where Headroom compression pays off; the opt-in adapter is already wired.
    messages = [
        {"role": "system", "content": "You are a financial macro analyst."},
        {"role": "user", "content": prompt}
    ]
    # Opt-in, default-off context compression (identity unless ALPHAIO_HEADROOM=1).
    from utils.headroom_compress import compress_messages
    messages = compress_messages(messages, surface="rag_macro")
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=messages
    )
    return response["choices"][0]["message"]["content"]

def evaluate_macro_threat_level():
    prompt = "What is the likely market impact of the next FOMC meeting and current CPI trends?"
    result = query_macro_context(prompt)
    print("[RAG MACRO] Macro insight:", result)
    return result

if __name__ == "__main__":
    evaluate_macro_threat_level()