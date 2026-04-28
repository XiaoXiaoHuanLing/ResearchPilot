"""SQLite/JSON Docstore 持久化管理。

存储所有层级节点+父子关系，供 AutoMergingRetriever 和 BM25 查询。
使用 SimpleDocumentStore + JSON 文件持久化。
"""

import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_docstore = None
_docstore_path = None


def get_docstore_path() -> Path:
    """获取 docstore 持久化目录"""
    docstore_dir = Path(settings.storage_base_dir) / "docstore"
    docstore_dir.mkdir(parents=True, exist_ok=True)
    return docstore_dir / "docstore.json"


def get_docstore():
    """获取或创建持久化 docstore（单例）

    使用 SimpleDocumentStore + JSON 文件持久化。
    数据存储在 storage/docstore/docstore.json
    """
    global _docstore
    if _docstore is not None:
        return _docstore

    docstore_file = get_docstore_path()

    if docstore_file.exists():
        try:
            from llama_index.core.storage.docstore import SimpleDocumentStore
            _docstore = SimpleDocumentStore.from_persist_path(str(docstore_file))
            logger.info("Loaded docstore from %s (%d docs)", docstore_file, len(_docstore.docs))
            return _docstore
        except Exception as e:
            logger.warning("Failed to load docstore from %s: %s, creating new", docstore_file, e)

    from llama_index.core.storage.docstore import SimpleDocumentStore
    _docstore = SimpleDocumentStore()
    logger.info("Created new docstore (persist path: %s)", docstore_file)
    return _docstore


def _persist_docstore():
    """持久化 docstore 到 JSON 文件"""
    global _docstore
    if _docstore is None:
        return

    try:
        docstore_file = get_docstore_path()
        _docstore.persist(str(docstore_file))
        logger.info("Persisted docstore to %s (%d docs)", docstore_file, len(_docstore.docs))
    except Exception as e:
        logger.error("Failed to persist docstore: %s", e)


def reset_docstore():
    """重置docstore单例（用于测试或重建）"""
    global _docstore
    _docstore = None


def add_nodes_to_docstore(nodes: list) -> int:
    """将节点添加到 docstore 并持久化

    Args:
        nodes: LlamaIndex节点列表（含叶子+非叶子+父子关系）

    Returns:
        添加的节点数
    """
    if not nodes:
        return 0

    store = get_docstore()

    try:
        store.add_documents(nodes)

        # 持久化
        _persist_docstore()

        logger.info("Added %d nodes to docstore", len(nodes))
        return len(nodes)
    except Exception as e:
        logger.error("Failed to add nodes to docstore: %s", e)
        return 0


def get_leaf_nodes_from_docstore() -> list:
    """从docstore加载所有叶子节点（BM25Retriever用）

    叶子节点 = 没有子节点的节点
    """
    store = get_docstore()

    try:
        if hasattr(store, 'docs'):
            all_nodes = list(store.docs.values())
        else:
            logger.warning("Docstore doesn't support node enumeration")
            return []

        leaf_nodes = []
        for node in all_nodes:
            children = getattr(node, 'children', None)
            if children is None or len(children) == 0:
                leaf_nodes.append(node)

        logger.info("Loaded %d leaf nodes from docstore (total: %d)", len(leaf_nodes), len(all_nodes))
        return leaf_nodes
    except Exception as e:
        logger.error("Failed to load leaf nodes: %s", e)
        return []


def delete_nodes_by_kb_doc_id(kb_doc_id: int) -> int:
    """从docstore删除指定文档的所有节点并持久化

    Args:
        kb_doc_id: KbDocumentModel.id

    Returns:
        删除的节点数
    """
    if not kb_doc_id:
        return 0

    store = get_docstore()
    deleted = 0

    try:
        if hasattr(store, 'docs'):
            keys_to_delete = []
            for key, node in list(store.docs.items()):
                metadata = getattr(node, 'metadata', {}) or {}
                if metadata.get("kb_doc_id") == kb_doc_id:
                    keys_to_delete.append(key)

            for key in keys_to_delete:
                try:
                    store.delete_document(key)
                    deleted += 1
                except Exception:
                    pass

        if deleted > 0:
            _persist_docstore()
            logger.info("Deleted %d docstore nodes for kb_doc_id=%d", deleted, kb_doc_id)
        return deleted
    except Exception as e:
        logger.error("Failed to delete docstore nodes for kb_doc_id=%d: %s", kb_doc_id, e)
        return 0
