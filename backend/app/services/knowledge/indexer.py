"""RAG入库服务 — 核心链路。

完整流程：解析 → 分层分块 → 入向量库 → 入docstore → 刷新BM25缓存
"""

import logging
import time
from pathlib import Path

from app.core.config import settings
from app.services.knowledge.parser import parse_file
from app.services.knowledge.chunker import SmartNodeParser
from app.services.knowledge.docstore import add_nodes_to_docstore
from app.services.knowledge.bm25 import invalidate_bm25_cache

logger = logging.getLogger(__name__)


async def index_document(
    file_path: str,
    kb_id: int,
    title: str = "",
    file_type: str = "text",
    source: str = "",
    metadata: dict | None = None,
) -> dict:
    """RAG索引服务：将文件入库

    完整链路：解析 → 分层分块 → 入向量库 → 入docstore → 刷新BM25

    Args:
        file_path: 文件路径（相对于storage_base_dir或绝对路径）
        kb_id: 目标知识库ID
        title: 文档标题
        file_type: 文件类型(md/pdf/txt/docx)
        source: 来源描述
        metadata: 额外元数据

    Returns:
        {leaf_count, total_count, latency_ms, eval_meta}
    """
    start_time = time.time()

    # 1. 解析文件
    documents = parse_file(file_path, file_type, title, source, metadata)
    if not documents:
        return {"leaf_count": 0, "total_count": 0, "latency_ms": 0}

    # 为每个文档添加KB元数据
    for doc in documents:
        doc.metadata["kb_id"] = kb_id
        doc.metadata["file_type"] = file_type
        if source:
            doc.metadata["source"] = source

    # 2. 分层分块
    parser = SmartNodeParser()
    leaf_nodes, all_nodes = parser.get_nodes(documents)

    logger.info(
        "Parsed %s: %d leaf nodes, %d total nodes",
        file_path, len(leaf_nodes), len(all_nodes),
    )

    # 3a. 入向量库（叶子节点）
    chunks_inserted = 0
    try:
        from app.services.knowledge.engine import _get_index
        idx = _get_index()
        if idx is not None:
            idx.insert_nodes(leaf_nodes)
            chunks_inserted = len(leaf_nodes)
            logger.info("Inserted %d leaf nodes into vector store", len(leaf_nodes))
        else:
            logger.warning("Vector index not available (embedding not configured), skipping vector insertion")
    except Exception as e:
        logger.error("Vector store insertion failed: %s", e)

    # 3b. 入docstore（所有节点+父子关系）
    docstore_count = add_nodes_to_docstore(all_nodes)

    # 4. 刷新BM25缓存 + 检索器缓存
    invalidate_bm25_cache()
    try:
        from app.services.knowledge.retriever import invalidate_retriever_cache
        invalidate_retriever_cache()
    except Exception:
        pass

    latency_ms = int((time.time() - start_time) * 1000)

    # ⭐ 评测接口预留
    eval_meta = {
        "operation": "index_document",
        "file_path": file_path,
        "file_type": file_type,
        "kb_id": kb_id,
        "leaf_count": len(leaf_nodes),
        "total_count": len(all_nodes),
        "chunks_inserted": chunks_inserted,
        "docstore_count": docstore_count,
        "latency_ms": latency_ms,
    }

    return {
        "leaf_count": len(leaf_nodes),
        "total_count": len(all_nodes),
        "latency_ms": latency_ms,
        "eval_meta": eval_meta,
    }


async def index_md_file(
    file_path: str,
    kb_id: int,
    title: str = "",
    metadata: dict | None = None,
) -> int:
    """将MD文件入库（报告入库专用）

    使用MarkdownNodeParser按标题层级分块，语义边界清晰。

    Args:
        file_path: MD文件路径
        kb_id: 目标知识库ID
        title: 文档标题
        metadata: 额外元数据

    Returns:
        入库chunk数
    """
    result = await index_document(
        file_path=file_path,
        kb_id=kb_id,
        title=title,
        file_type="md",
        source=f"报告入库: {title}",
        metadata=metadata,
    )
    return result.get("leaf_count", 0)
