"""Lightweight RAG service using OpenAI embeddings + numpy cosine similarity.

This avoids the heavy LlamaIndex/torch dependency chain while providing
functional retrieval-augmented generation for the MVP stage.
"""

import json
import logging
import os
from pathlib import Path

import numpy as np
from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# Vector store persistence path
VEC_STORE_DIR = Path(__file__).resolve().parents[2] / "vector_store"
VEC_INDEX_PATH = VEC_STORE_DIR / "index.json"
VEC_NPY_PATH = VEC_STORE_DIR / "embeddings.npy"

_client: OpenAI | None = None


def _get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )
    return _client


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between vector a and matrix b."""
    a_norm = a / (np.linalg.norm(a) + 1e-10)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
    return a_norm @ b_norm.T


class VectorStore:
    """Simple file-backed vector store for article embeddings."""

    def __init__(self) -> None:
        self._ids: list[int] = []
        self._texts: list[str] = []
        self._embeddings: np.ndarray | None = None
        VEC_STORE_DIR.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if VEC_INDEX_PATH.exists() and VEC_NPY_PATH.exists():
            with open(VEC_INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._ids = data.get("ids", [])
            self._texts = data.get("texts", [])
            self._embeddings = np.load(str(VEC_NPY_PATH))
            logger.info("Loaded vector store: %d entries", len(self._ids))

    def _save(self) -> None:
        with open(VEC_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump({"ids": self._ids, "texts": self._texts}, f, ensure_ascii=False, indent=2)
        if self._embeddings is not None:
            np.save(str(VEC_NPY_PATH), self._embeddings)

    @property
    def size(self) -> int:
        return len(self._ids)

    def has(self, article_id: int) -> bool:
        return article_id in self._ids

    async def add(self, article_id: int, text: str) -> None:
        """Embed and add an article to the store."""
        client = _get_openai_client()
        resp = client.embeddings.create(
            model=settings.embedding_model,
            input=text[:8000],
        )
        vec = np.array(resp.data[0].embedding, dtype=np.float32)

        if article_id in self._ids:
            idx = self._ids.index(article_id)
            self._texts[idx] = text
            self._embeddings[idx] = vec
        else:
            self._ids.append(article_id)
            self._texts.append(text)
            if self._embeddings is None:
                self._embeddings = vec.reshape(1, -1)
            else:
                self._embeddings = np.vstack([self._embeddings, vec])

        self._save()
        logger.info("Added article %d to vector store", article_id)

    async def search(self, query: str, top_k: int = 5) -> list[tuple[int, str, float]]:
        """Search for top_k most similar articles."""
        if self._embeddings is None or len(self._ids) == 0:
            return []

        client = _get_openai_client()
        resp = client.embeddings.create(
            model=settings.embedding_model,
            input=query,
        )
        query_vec = np.array(resp.data[0].embedding, dtype=np.float32)

        sims = _cosine_similarity(query_vec, self._embeddings)[0]
        top_indices = np.argsort(sims)[::-1][:top_k]

        results = []
        for idx in top_indices:
            results.append((self._ids[idx], self._texts[idx], float(sims[idx])))
        return results

    def remove(self, article_id: int) -> None:
        if article_id not in self._ids:
            return
        idx = self._ids.index(article_id)
        self._ids.pop(idx)
        self._texts.pop(idx)
        if self._embeddings is not None and len(self._embeddings) > 0:
            self._embeddings = np.delete(self._embeddings, idx, axis=0)
        self._save()


# Singleton
vector_store = VectorStore()


async def rag_query(question: str, top_k: int = 5) -> dict:
    """Run a RAG query: retrieve relevant articles, then generate an answer."""
    # 1. Retrieve
    results = await vector_store.search(question, top_k=top_k)

    if not results:
        return {
            "question": question,
            "answer": "当前知识库中没有足够的收藏资讯来回答此问题。请先收藏一些相关资讯后重试。",
            "citations": [],
        }

    # 2. Build context from retrieved articles
    context_parts = []
    citations = []
    for article_id, text, score in results:
        context_parts.append(f"[来源ID:{article_id}] {text[:2000]}")
        citations.append({
            "article_id": article_id,
            "relevance_score": round(score, 4),
            "snippet": text[:300],
        })

    context = "\n\n---\n\n".join(context_parts)

    # 3. Generate answer using LLM
    if settings.openai_api_key:
        client = _get_openai_client()
        system_prompt = (
            "你是 ResearchPilot 研究助手。基于提供的参考资料回答用户问题。"
            "回答时应引用来源ID，保持客观、准确。如果参考资料不足以回答问题，请说明。"
        )
        try:
            resp = client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"参考资料：\n{context}\n\n用户问题：{question}"},
                ],
                temperature=0.3,
                max_tokens=1500,
            )
            answer = resp.choices[0].message.content or ""
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            answer = f"LLM 调用失败（{e}），以下是检索到的相关资讯摘要：\n\n" + "\n".join(
                c["snippet"][:200] for c in citations
            )
    else:
        # Fallback: no LLM, just return retrieved context
        answer = "（未配置 LLM API，以下为检索到的相关资讯摘要）\n\n" + "\n\n---\n\n".join(
            c["snippet"] for c in citations
        )

    return {
        "question": question,
        "answer": answer,
        "citations": citations,
    }


async def index_article(article_id: int, title: str, summary: str, content: str) -> None:
    """Index a single article into the vector store."""
    text = f"标题：{title}\n摘要：{summary}\n正文：{content}"
    await vector_store.add(article_id, text)


async def reindex_all_articles(db) -> None:
    """Re-index all bookmarked articles from DB into the vector store."""
    from app.db.models import ArticleModel

    articles = db.query(ArticleModel).filter(ArticleModel.bookmarked == True).all()  # noqa: E712
    for article in articles:
        text = f"标题：{article.title}\n摘要：{article.summary}\n正文：{article.content}"
        await vector_store.add(article.id, text)
    logger.info("Re-indexed %d bookmarked articles", len(articles))
