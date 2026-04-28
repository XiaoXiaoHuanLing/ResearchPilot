"""SmartNodeParser — 按文档类型选择分层分块策略。

MD文件: MarkdownNodeParser（按标题层级，零成本语义边界）
其他文件: HierarchicalNodeParser（多粒度层级分块）
向量库只存叶子节点，docstore存所有节点+父子关系。
"""

import logging

from llama_index.core import Document
from llama_index.core.node_parser import (
    MarkdownNodeParser,
    HierarchicalNodeParser,
    SentenceSplitter,
)

logger = logging.getLogger(__name__)

# 默认分层粒度：粗→细
DEFAULT_CHUNK_SIZES = [2048, 512, 256]


class SmartNodeParser:
    """按文档类型选择分块策略，统一输出带层级关系的节点

    输出:
    - leaf_nodes: 最细粒度节点，入向量库
    - all_nodes: 所有层级节点+父子关系，入docstore
    """

    def __init__(self, chunk_sizes: list[int] | None = None):
        self.chunk_sizes = chunk_sizes or DEFAULT_CHUNK_SIZES
        self.md_parser = MarkdownNodeParser()
        self.hier_parser = HierarchicalNodeParser.from_defaults(
            chunk_sizes=self.chunk_sizes
        )
        self.sentence_splitter = SentenceSplitter(
            chunk_size=self.chunk_sizes[-1],
            chunk_overlap=20,
        )

    def get_nodes(self, documents: list[Document]) -> tuple[list, list]:
        """解析文档，返回 (leaf_nodes, all_nodes)

        leaf_nodes: 最细粒度节点，入向量库
        all_nodes: 所有层级节点+父子关系，入docstore
        """
        all_nodes = []

        for doc in documents:
            file_type = doc.metadata.get("file_type", "text")

            if file_type in ("md", "markdown"):
                # MD: 按标题层级解析
                section_nodes = self.md_parser.get_nodes_from_documents([doc])
                section_nodes = self._refine_oversized(section_nodes, doc)
            else:
                # 其他: 层级分块
                try:
                    section_nodes = self.hier_parser.get_nodes_from_documents([doc])
                except Exception as e:
                    logger.warning("HierarchicalNodeParser failed, falling back to SentenceSplitter: %s", e)
                    section_nodes = self.sentence_splitter.get_nodes_from_documents([doc])

            all_nodes.extend(section_nodes)

        # 分离叶子节点和中间节点
        leaf_nodes = []
        non_leaf_nodes = []

        for node in all_nodes:
            # 判断是否叶子节点：没有子节点 或 children为空
            children = getattr(node, 'children', None)
            if children is None or len(children) == 0:
                leaf_nodes.append(node)
            else:
                non_leaf_nodes.append(node)

        # 如果所有节点都没有层级关系（如SentenceSplitter降级），全部视为叶子
        if not leaf_nodes and all_nodes:
            leaf_nodes = all_nodes

        logger.info(
            "SmartNodeParser: %d docs → %d leaf nodes, %d total nodes",
            len(documents), len(leaf_nodes), len(all_nodes),
        )

        return leaf_nodes, all_nodes

    def _refine_oversized(self, nodes: list, parent_doc: Document) -> list:
        """超过叶子粒度上限的节点用句子边界细分，正确建立父子关系"""
        from llama_index.core.schema import NodeRelationship, RelatedNodeInfo

        refined = []
        max_leaf_size = self.chunk_sizes[-1] * 1.5

        for node in nodes:
            text = getattr(node, 'text', '') or getattr(node, 'get_content', lambda: '')()
            if len(text) > max_leaf_size:
                # 细分
                sub_doc = Document(text=text, metadata={**node.metadata, "refined_from": node.node_id})
                sub_nodes = self.sentence_splitter.get_nodes_from_documents([sub_doc])

                # 建立 LlamaIndex 原生父子关系（AutoMerging 依赖此关系）
                child_ids = []
                for sub in sub_nodes:
                    sub.relationships[NodeRelationship.PARENT] = RelatedNodeInfo(node_id=node.node_id)
                    child_ids.append(RelatedNodeInfo(node_id=sub.node_id))
                    sub.metadata["parent_node_id"] = node.node_id
                    sub.metadata["ref_doc_id"] = parent_doc.doc_id

                # 父节点记录子节点
                node.relationships[NodeRelationship.CHILD] = child_ids

                refined.append(node)  # 保留父节点
                refined.extend(sub_nodes)
            else:
                refined.append(node)

        return refined
