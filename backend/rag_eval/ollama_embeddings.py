"""Custom LangChain Embeddings wrapper for ollama.

Bypasses LangChain's tiktoken tokenization which breaks with ollama
(model name with colon, token IDs instead of strings).
"""
from typing import List
from langchain_core.embeddings import Embeddings
from openai import OpenAI


class OllamaEmbeddings(Embeddings):
    """Minimal Embeddings wrapper that sends raw strings to ollama."""

    def __init__(self, model: str, base_url: str, api_key: str = "ollama"):
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        resp = self.client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]

    def embed_query(self, text: str) -> List[float]:
        resp = self.client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding
