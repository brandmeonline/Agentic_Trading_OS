# rag_macro.py – plug macro context into signal confidence scoring
import openai

def query_macro_context(prompt):
    # Placeholder using OpenAI - will be integrated with a macro source + vector DB
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a financial macro analyst."},
            {"role": "user", "content": prompt}
        ]
    )
    return response["choices"][0]["message"]["content"]

def evaluate_macro_threat_level():
    prompt = "What is the likely market impact of the next FOMC meeting and current CPI trends?"
    result = query_macro_context(prompt)
    print("[RAG MACRO] Macro insight:", result)
    return result

if __name__ == "__main__":
    evaluate_macro_threat_level()