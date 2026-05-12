"""RAG 评估数据集生成模块。

自定义管线：原文档 → 分组切分 → LLM prompt 生成 Q&A → 质量筛选 → JSONL 数据集。
评估用 RAGAS evaluate()，数据集构建由本模块完成。

核心设计：
  - 分组策略：5个相邻 chunk 一组（overlap=1），保证上下文连贯
  - 问题类型：fact / keyword / vague / negation，强制比例
  - 难度分级：easy(1节点) / medium(2-3节点) / hard(3+节点)
  - 质量筛选：字段完整性 → 常识过滤 → 去重
  - JSON 容错解析：兼容 markdown 代码块包裹等

两种输入路径：
  - 路径 A: 指定本地文档目录
  - 路径 B: 指定已入库的 KB ID

产出: JSONL 格式数据集（字段对齐 RAGAS SingleTurnSample）
"""

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

# ─── 数据目录 ──────────────────────────────────────────────────

_MODULE_DIR = Path(__file__).resolve().parent
_DATA_DIR = _MODULE_DIR / "data"
_DATASETS_DIR = _DATA_DIR / "datasets"
_DATASETS_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 文档加载
# ═══════════════════════════════════════════════════════════════


def load_local_documents(doc_dir: str | Path) -> list[Document]:
    """从本地目录加载文档（支持 .docx），返回 LangChain Document 列表。"""
    import docx as python_docx

    doc_dir = Path(doc_dir)
    if not doc_dir.is_dir():
        raise FileNotFoundError(f"文档目录不存在: {doc_dir}")

    documents = []
    for fp in sorted(doc_dir.glob("*.docx")):
        try:
            doc = python_docx.Document(str(fp))
            text = "\n\n".join(
                para.text for para in doc.paragraphs if para.text.strip()
            )
            if not text.strip():
                logger.warning("跳过空文档: %s", fp.name)
                continue
            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "source": str(fp),
                        "title": fp.stem,
                        "file_name": fp.name,
                    },
                )
            )
            logger.info("加载文档: %s (%d 字符)", fp.name, len(text))
        except Exception as e:
            logger.error("加载文档失败 %s: %s", fp.name, e)

    if not documents:
        raise ValueError(f"目录 {doc_dir} 中没有可用的 .docx 文档")

    logger.info("共加载 %d 篇文档", len(documents))
    return documents


async def load_kb_documents(kb_id: int) -> list[Document]:
    """从已入库 KB 加载文档原文。"""
    import sys

    backend_dir = str(Path(__file__).resolve().parents[1])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from app.db.session import SessionLocal
    from app.db.models import KbDocumentModel
    import docx as python_docx

    documents = []
    with SessionLocal() as db:
        kb_docs = (
            db.query(KbDocumentModel)
            .filter(KbDocumentModel.kb_id == kb_id)
            .all()
        )
        for kd in kb_docs:
            fp = Path(kd.file_path) if kd.file_path else None
            if not fp or not fp.exists():
                from app.core.config import settings
                fp = Path(settings.storage_base_dir) / "kb_docs" / str(kb_id) / kd.filename
            if not fp.exists():
                logger.warning("跳过找不到的文档: %s", kd.title)
                continue
            try:
                doc = python_docx.Document(str(fp))
                text = "\n\n".join(
                    para.text for para in doc.paragraphs if para.text.strip()
                )
                if not text.strip():
                    continue
                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "source": str(fp),
                            "title": kd.title,
                            "file_name": Path(kd.file_path).name if kd.file_path else kd.title,
                            "kb_doc_id": kd.id,
                        },
                    )
                )
            except Exception as e:
                logger.error("加载 KB 文档失败 %s: %s", kd.title, e)

    if not documents:
        raise ValueError(f"KB #{kb_id} 中没有可用的文档")

    logger.info("从 KB #%d 加载 %d 篇文档", kb_id, len(documents))
    return documents


# ═══════════════════════════════════════════════════════════════
# 文档切分 + 分组
# ═══════════════════════════════════════════════════════════════

# 高密度内容指示：数值+单位、参数赋值、英文术语、阈值比较、代码片段
_HIGH_DENSITY_PATTERN = re.compile(
    r'(\d+\.?\d*\s*[%毫秒GBMBKB次条个台]|'
    r'[a-zA-Z_]\w*\s*[=：:]\s*|'
    r'[a-zA-Z_]{3,}|'
    r'[<>]=?\s*\d+|'
    r'`[^`]+`|'
    r'http[s]?://|'
    r'\{[^}]+\})'
)


def _is_high_density(text: str) -> bool:
    """判断短文本是否是高密度干货（含数值/参数/术语/代码）。"""
    if not text.strip():
        return False
    return bool(_HIGH_DENSITY_PATTERN.search(text))


def chunk_documents(
    documents: list[Document],
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> list[Document]:
    """将文档切分为固定大小的块，用于分组。

    chunk_size=1000 略大于 RAG 主链路的 ~500，因为评估数据需要
    更完整的上下文来生成有意义的问答。分组时 5 个 chunk 一组
    ≈ 3000-5000 字，足以支撑 medium/hard 问题。

    Args:
        documents: 原始文档列表
        chunk_size: 每块最大字符数
        chunk_overlap: 块间重叠字符数

    Returns:
        切分后的 Document 列表（每块带 chunk_index metadata）
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        length_function=len,
    )

    chunks = []
    for doc in documents:
        doc_chunks = splitter.split_documents([doc])
        for idx, chunk in enumerate(doc_chunks):
            chunk.metadata["chunk_index"] = idx
            chunk.metadata["doc_title"] = doc.metadata.get("title", "")
            chunks.append(chunk)

    # 过滤低价值 chunk：极短且无高密度内容
    valuable_chunks = []
    for chunk in chunks:
        text = chunk.page_content
        if len(text.strip()) < 30 and not _is_high_density(text):
            continue
        valuable_chunks.append(chunk)

    logger.info(
        "切分完成: %d 篇文档 → %d 个切分块 (过滤后 %d)",
        len(documents), len(chunks), len(valuable_chunks),
    )
    return valuable_chunks


def group_chunks_by_doc(
    chunks: list[Document],
    group_size: int = 5,
    overlap: int = 1,
) -> list[list[Document]]:
    """将同一文档的 chunk 按顺序分组，带重叠。

    分组策略：5 个 chunk 一组，overlap=1 保证跨组上下文连续。
    每组 ≈ 3000-5000 字上下文，足以支撑多事实点归纳和跨段推理。

    Args:
        chunks: 切分块列表
        group_size: 每组 chunk 数
        overlap: 组间重叠 chunk 数

    Returns:
        分组列表
    """
    # 按文档分组
    doc_chunks: dict[str, list[Document]] = {}
    for chunk in chunks:
        doc_title = chunk.metadata.get("doc_title", "unknown")
        doc_chunks.setdefault(doc_title, []).append(chunk)

    all_groups = []
    for doc_title, doc_chunk_list in doc_chunks.items():
        step = group_size - overlap
        for start in range(0, len(doc_chunk_list), step):
            group = doc_chunk_list[start:start + group_size]
            if len(group) >= 2:
                all_groups.append(group)
            if start + group_size >= len(doc_chunk_list):
                break

    logger.info(
        "分组完成: %d 个 chunk → %d 组 (group_size=%d, overlap=%d)",
        len(chunks), len(all_groups), group_size, overlap,
    )
    return all_groups


# ═══════════════════════════════════════════════════════════════
# Q&A 生成 Prompt
# ═══════════════════════════════════════════════════════════════

_QA_GENERATION_PROMPT = """你是一个RAG评估数据集构造专家。以下是来自同一文档的 {group_size} 个内容节点。
请基于这些节点生成评估用问答对。

## 节点内容

{nodes_text}

## 【绝对不可违反的核心规则】

1. 每个问题的答案必须100%完整地包含在你标注的依赖节点中，与之外的其他节点无关
2. 任何不相关的节点都绝对不能回答这个问题（即问题的答案只存在于你标注的依赖节点里）
3. 问题必须足够具体，不能有任何歧义
4. 绝对不能生成可以通过常识回答的问题（必须依赖本知识库的专业/内部信息）
5. 绝对不能生成答案在节点中只出现一次且非常隐蔽的问题（如某个不起眼的数值埋在长段落末尾）
6. 问题要符合真实用户的提问习惯，不要使用生硬的学术表述
7. **问题必须自包含（SELF-CONTAINED）**：问题脱离文档上下文后仍能被理解。禁止使用"本规范"、"本文档"、"本系统"、"上述"、"案例2中"等依赖上下文的代词和指代。必须写明具体名称，如"AI系统安全加固与权限管理规范"而非"本规范"，"Kubernetes GPU集群"而非"本系统"。
8. 问题的主语/限定词必须明确："X规范要求兼容的MLflow版本为_____"而非"要求兼容的MLflow版本为_____"
9. **人类可回答性检验**：想象一个对该领域有一定了解但未看过本文档的专业人员，仅凭问题本身能否理解要查什么？如果人类拿到问题后不知道该去哪份文档、哪个章节查答案，那么这个问题就是不合格的。每个问题都必须满足：人类看到问题后能明确知道需要查找的主题范围和关键概念。

## 【多节点综合优先】

优先生成需要综合多个节点信息才能回答的问题。
如果一个事实只用1个节点就能完整回答，且该事实过于简单（如单个数值），优先级较低。

## 【类型多样性强制要求】

你生成的每个问题必须覆盖不同的类型，禁止全部生成fact型！
每组节点最多生成1个fact型问题，其余必须是其他类型。
各类型比例目标：fact ≤ 30%, keyword ≥ 25%, vague ≥ 20%, negation ≥ 25%

- **fact**：原文挖空型，将原文中的具体数值/参数/名称/版本号挖空形成填空式问题。问题必须自包含，写明来源文档/系统名称，如"AI系统安全加固与权限管理规范要求兼容的MLflow版本为_____"。答案必须精确为被挖空的原文片段（如"2.10及以上"），不得概括或改写。问题中用_____标记被挖空位置。答案长度≤30字。（最多1个/组）
- **keyword**：包含特定技术术语的精确查询，问题中必须出现至少一个专业术语/英文缩写/参数名，如"simple_ratio_thresh参数的含义"、"K8s NetworkPolicy的默认行为"。问题必须自包含，禁止"本规范中XX参数"等指代。（必出）
- **vague**：模糊短查询（≤10字），如"检索策略"、"告警阈值"、"GPU降频"。简短但指向明确的技术领域，不依赖上下文也能理解。（必出）
- **negation**：否定/验证式问题，含"是否"、"能否"、"允许"、"禁止"等否定/验证语义。必须指明具体系统/文档名称，如"Kubernetes GPU集群运维指南是否允许GPU超分？"，禁止"是否允许XX"（缺失主语）。（必出）

## 【难度定义】

- **easy**：仅依赖1个节点，单事实点直接定位
- **medium**：需综合2-3个节点信息，多事实点梳理归纳
- **hard**：需综合3个以上节点，跨段推理、对比分析、因果链

## 【输出必填字段】

每个问题的 answer、key_phrases 四个字段必须全部填写，不得遗漏。
- answer：至少2-3句话，包含必要的细节和推理
- key_phrases：2-5个核心关键词/术语

## 输出格式

输出JSON数组。如果这些节点中没有值得出题的独立信息点，输出空数组 []。
宁可少输出，也不要输出凑数的问题。

```json
[
  {{
    "question": "问题文本",
    "question_type": "fact/keyword/vague/negation",
    "difficulty": "easy/medium/hard",
    "dependency_node_indices": [0, 2],
    "answer": "基于依赖节点的完整答案，至少2-3句话，包含必要的细节和推理",
    "key_phrases": ["关键词1", "关键词2", "关键词3"]
  }}
]
```"""


def _format_nodes_for_prompt(chunks: list[Document], max_chars_per_node: int = 800) -> str:
    """格式化节点文本，每个节点标注索引 + 截断内容。"""
    parts = []
    for i, chunk in enumerate(chunks):
        text = chunk.page_content
        if len(text) > max_chars_per_node:
            text = text[:max_chars_per_node] + "..."
        parts.append(f"[节点 {i}] {text}")
    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# JSON 容错解析
# ═══════════════════════════════════════════════════════════════


def parse_json_response(response: str) -> list[dict]:
    """从 LLM 响应中解析 JSON 数组（容忍 markdown 代码块包裹）。

    策略：
    1. 提取 ```json ... ``` 内容
    2. 找最外层 [ ] 之间的内容
    3. json.loads 整段文本
    4. 全部失败 → 返回空列表
    """
    # 尝试提取 markdown 代码块
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
    if match:
        json_str = match.group(1).strip()
    else:
        json_str = response.strip()

    # 尝试直接解析
    try:
        result = json.loads(json_str)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
    except json.JSONDecodeError:
        pass

    # 尝试找最外层 [ ]
    start = json_str.find("[")
    end = json_str.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(json_str[start:end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    logger.warning("JSON 解析失败: %s", response[:200])
    return []


# ═══════════════════════════════════════════════════════════════
# Q&A 生成
# ═══════════════════════════════════════════════════════════════


async def generate_qa_for_group(
    chunks: list[Document],
    llm,
) -> list[dict]:
    """为一组 chunk 生成 Q&A 对。

    Args:
        chunks: 一组相邻 chunk（通常 5 个）
        llm: LangChain ChatOpenAI 实例

    Returns:
        Q&A 记录列表
    """
    from langchain_core.messages import HumanMessage

    # 过滤极短节点
    valid_chunks = [c for c in chunks if len(c.page_content.strip()) >= 30]
    if len(valid_chunks) < 2:
        return []

    nodes_text = _format_nodes_for_prompt(valid_chunks)
    if len(nodes_text.strip()) < 100:
        return []

    prompt = _QA_GENERATION_PROMPT.format(
        group_size=len(valid_chunks),
        context=nodes_text,
        nodes_text=nodes_text,
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        text = response.content or ""
    except Exception as e:
        logger.error("LLM 生成 Q&A 失败: %s", e)
        return []

    # JSON 容错解析
    qa_list = parse_json_response(text)
    if not qa_list:
        logger.warning("JSON 解析为空，跳过该组")
        return []

    # 转换为标准记录格式
    records = []
    for qa in qa_list:
        question = qa.get("question", "").strip()
        answer = qa.get("answer", "").strip()
        question_type = qa.get("question_type", "fact").strip().lower()
        difficulty = qa.get("difficulty", "medium").strip().lower()
        key_phrases = qa.get("key_phrases", [])
        dep_indices = qa.get("dependency_node_indices", [])

        # 校验 dependency_node_indices 范围
        valid_indices = [idx for idx in dep_indices if 0 <= idx < len(valid_chunks)]

        # 收集依赖节点的原文
        ref_contexts = [valid_chunks[idx].page_content for idx in valid_indices]
        if not ref_contexts:
            ref_contexts = [c.page_content for c in valid_chunks]

        # 收集来源文档路径
        doc_paths = set()
        for idx in valid_indices:
            chunk = valid_chunks[idx]
            for key in ("title", "file_name", "source"):
                val = chunk.metadata.get(key, "")
                if val:
                    doc_paths.add(val)

        record = {
            "user_input": question,
            "reference": answer,
            "reference_contexts": ref_contexts,
            "expected_doc_paths": sorted(doc_paths),
            "question_type": question_type,
            "difficulty": difficulty,
            "key_phrases": key_phrases,
            "dependency_count": len(valid_indices),
        }
        records.append(record)

    return records


# ═══════════════════════════════════════════════════════════════
# 质量筛选
# ═══════════════════════════════════════════════════════════════


def filter_quality(records: list[dict], llm=None) -> list[dict]:
    """三层质量筛选：字段完整性 → 常识过滤 → 去重。

    Args:
        records: 原始记录列表
        llm: LangChain ChatOpenAI（可选，用于补全缺失字段）

    Returns:
        筛选后的记录列表
    """
    # ── 第一层：字段完整性 ──
    complete = []
    for rec in records:
        q = rec.get("user_input", "")
        a = rec.get("reference", "")
        kp = rec.get("key_phrases", [])
        qt = rec.get("question_type", "")
        diff = rec.get("difficulty", "")

        # 必填字段非空
        if not q or len(q) < 5:
            continue
        if not a or len(a) < 20:
            continue
        if qt not in ("fact", "keyword", "vague", "negation"):
            rec["question_type"] = "fact"  # 默认值
        if diff not in ("easy", "medium", "hard"):
            rec["difficulty"] = "medium"  # 默认值
        if not kp:
            # 自动从 question 提取简单关键词
            kp = re.findall(r'[a-zA-Z_]{3,}|[一-鿿]{2,6}', q)
            rec["key_phrases"] = kp[:5]

        complete.append(rec)

    logger.info("字段完整性过滤: %d → %d", len(records), len(complete))

    # ── 第1.5层：自包含校验 ──
    # 过滤问题中包含依赖上下文的指代词（脱离文档后无法理解）
    _CONTEXT_DEPS = re.compile(r'本规范|本文档|本系统|本平台|本指南|本手册|本标准|本方案|该规范|该文档|上述|案例\d|案例[一二三四五六七八九十]')
    self_contained = []
    for rec in complete:
        q = rec.get("user_input", "")
        if _CONTEXT_DEPS.search(q):
            logger.debug("问题不自包含(含指代词)，丢弃: %s", q[:50])
            continue
        self_contained.append(rec)

    logger.info("自包含校验: %d → %d", len(complete), len(self_contained))

    # ── 第1.6层：人类可回答性校验 ──
    # 问题中必须包含至少一个能定位到具体领域/文档/系统的关键词或技术术语
    # 如果人类拿到问题后不知道该查哪份文档，说明问题不合格
    _DOMAIN_INDICATORS = re.compile(
        r'[A-Z][a-zA-Z]{2,}|[a-z_]{3,}|\d+\.\d+'  # 英文术语/版本号
        r'|GPU|K8s|Kubernetes|MLflow|DVC|vLLM|Triton|NCCL|CUDA'  # 核心技术
        r'|nvidia|pod|rbac|ecc|mig|dcgm|ssh|tls|aes|grpc|helm'  # 常见缩写
        r'|安全|加固|权限|运维|故障|监控|部署|推理|训练|调度|告警'  # 中文领域词
        r'|规范|指南|手册|策略|阈值|检查|集群|容器|存储|网络',  # 中文技术词
        re.IGNORECASE
    )
    human_answerable = []
    for rec in self_contained:
        q = rec.get("user_input", "")
        # 至少需要一个领域指示词
        if not _DOMAIN_INDICATORS.search(q):
            logger.debug("问题无法定位领域(人类无法确定查阅方向)，丢弃: %s", q[:50])
            continue
        human_answerable.append(rec)

    logger.info("人类可回答性校验: %d → %d", len(self_contained), len(human_answerable))

    # ── 第二层：常识过滤 ──
    non_common = []
    for rec in human_answerable:
        kp = rec.get("key_phrases", [])
        answer = rec.get("reference", "")

        # 如果所有 key_phrases 都是日常用语且 answer 无专业术语/参数/数值 → 疑似常识
        has_tech_content = bool(_HIGH_DENSITY_PATTERN.search(answer))
        has_tech_phrase = any(
            _HIGH_DENSITY_PATTERN.search(p) for p in kp if isinstance(p, str)
        )

        if not has_tech_content and not has_tech_phrase and len(kp) <= 2:
            logger.debug("疑似常识问题，丢弃: %s", rec["user_input"][:50])
            continue

        non_common.append(rec)

    logger.info("常识过滤: %d → %d", len(complete), len(non_common))

    # ── 第三层：去重 ──
    unique = []
    seen_questions = set()
    seen_phrases = []

    for rec in non_common:
        q_norm = re.sub(r'\s+', '', rec["user_input"]).lower()

        # 精确去重
        if q_norm in seen_questions:
            continue
        seen_questions.add(q_norm)

        # 模糊去重：key_phrases 重叠度 > 80%
        kp_set = set(p.lower() for p in rec.get("key_phrases", []) if isinstance(p, str))
        is_dup = False
        for prev_phrases in seen_phrases:
            if not kp_set or not prev_phrases:
                continue
            overlap = len(kp_set & prev_phrases) / max(len(kp_set), len(prev_phrases))
            if overlap > 0.8:
                is_dup = True
                break

        if is_dup:
            logger.debug("模糊去重，丢弃: %s", rec["user_input"][:50])
            continue

        seen_phrases.append(kp_set)
        unique.append(rec)

    logger.info("去重: %d → %d", len(non_common), len(unique))
    return unique


# ═══════════════════════════════════════════════════════════════
# LLM 构建
# ═══════════════════════════════════════════════════════════════

# 模型优先级列表：高质量模型优先，额度耗尽自动切换
_MODEL_PRIORITY = [
    "qwen3.6-plus",
    "qwen3.6-max-preview",
    "qwen3.6-27b",
    "qwen3.6-plus-2026-04-02",
    "qwen3.5-plus-2026-04-20",
    "qwen3.5-plus-2026-02-15",
    "qwen3.5-397b-a17b",
]


def _build_llm(model: str | None = None, temperature: float = 0.7, max_tokens: int = 3000):
    """构建 LangChain ChatOpenAI（用于 Q&A 生成）。

    Args:
        model: 指定模型名，None 则用 config 配置
        temperature: 生成温度
        max_tokens: 最大输出 token
    """
    import sys

    backend_dir = str(Path(__file__).resolve().parents[1])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from langchain_openai import ChatOpenAI
    from app.core.config import settings

    model_name = model or settings.eval_llm_model
    return ChatOpenAI(
        model=model_name,
        api_key=settings.eval_llm_api_key,
        base_url=settings.eval_llm_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=180,
    )


def _build_llm_with_fallback() -> list:
    """构建多个 LLM 实例，用于 fallback。

    返回 ChatOpenAI 实例列表，按优先级排序。
    """
    llms = []
    for model in _MODEL_PRIORITY:
        try:
            llm = _build_llm(model=model, temperature=0.7, max_tokens=3000)
            llms.append(llm)
        except Exception:
            continue
    if not llms:
        llms.append(_build_llm())  # fallback 到 config 配置
    return llms


async def _invoke_with_fallback(llms: list, prompt: str) -> str | None:
    """带 fallback 的 LLM 调用。403/429 自动切换下一个模型。"""
    from langchain_core.messages import HumanMessage

    for llm in llms:
        try:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            text = response.content or ""
            if text.strip():
                return text
        except Exception as e:
            err = str(e)
            if "403" in err or "429" in err or "AllocationQuota" in err:
                logger.warning("模型 %s 额度耗尽，切换下一个", llm.model_name)
                continue
            logger.error("LLM 调用失败 (%s): %s", llm.model_name, err[:100])
            continue
    return None


# ═══════════════════════════════════════════════════════════════
# 断点续建
# ═══════════════════════════════════════════════════════════════


def _save_checkpoint(records: list[dict], group_idx: int, path: Path):
    """保存中间结果到 checkpoint 文件。"""
    data = {
        "records": records,
        "group_idx": group_idx,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Checkpoint 保存失败: %s", e)


def _load_checkpoint(path: Path) -> dict | None:
    """加载 checkpoint 文件。"""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(
            "加载 checkpoint: %d 条记录, group_idx=%d",
            len(data.get("records", [])),
            data.get("group_idx", 0),
        )
        return data
    except Exception as e:
        logger.warning("Checkpoint 加载失败: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════


async def generate_dataset(
    doc_dir: str | Path | None = None,
    kb_id: int | None = None,
    testset_size: int = 200,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
    group_size: int = 5,
    group_overlap: int = 1,
    checkpoint_every: int = 10,
) -> Path:
    """生成 RAG 评估数据集。

    流程：
    1. 加载原文档
    2. 切分 + 分组（5 chunk/组，1 重叠）
    3. 均匀采样组
    4. 逐组 LLM 生成 Q&A（JSON 输出 + 容错解析）
    5. 三层质量筛选（完整性 → 常识过滤 → 去重）
    6. JSONL 持久化 + meta.json

    Args:
        doc_dir: 本地文档目录路径
        kb_id: 已入库的知识库 ID
        testset_size: 目标条数，默认 200
        chunk_size: 切分块大小（字符），默认 1000
        chunk_overlap: 切分重叠（字符），默认 150
        group_size: 每组 chunk 数，默认 5
        group_overlap: 组间重叠 chunk 数，默认 1
        checkpoint_every: 每 N 组保存一次 checkpoint，0 表示不保存

    Returns:
        数据集 JSONL 文件路径
    """
    # 1. 加载文档
    if doc_dir:
        documents = load_local_documents(doc_dir)
    elif kb_id is not None:
        documents = await load_kb_documents(kb_id)
    else:
        raise ValueError("必须指定 doc_dir 或 kb_id")

    logger.info("开始生成数据集: %d 篇文档, 目标 %d 条", len(documents), testset_size)

    # 2. 切分 + 分组
    chunks = chunk_documents(documents, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    groups = group_chunks_by_doc(chunks, group_size=group_size, overlap=group_overlap)

    # 3. 均匀采样（避免长文档主导）
    random.seed(42)
    # 估算每组平均产出 3-5 条 Q&A
    estimated_per_group = 4
    total_needed = max(1, testset_size // estimated_per_group)

    if len(groups) > total_needed:
        selected_groups = random.sample(groups, total_needed)
    else:
        selected_groups = groups

    logger.info(
        "采样 %d/%d 组 (每组 ≈ %d 条)",
        len(selected_groups), len(groups), estimated_per_group,
    )

    # 4. LLM 生成 Q&A（带 fallback + checkpoint）
    llms = _build_llm_with_fallback()
    all_records = []
    t0 = time.time()

    # 尝试加载 checkpoint
    ckpt_path = _DATASETS_DIR / "_checkpoint.json"
    ckpt = _load_checkpoint(ckpt_path)
    start_idx = 0
    if ckpt:
        all_records = ckpt.get("records", [])
        start_idx = ckpt.get("group_idx", 0) + 1
        logger.info("从 checkpoint 恢复: %d 条, 从第 %d 组继续", len(all_records), start_idx)

    for i, group in enumerate(selected_groups):
        if i < start_idx:
            continue

        try:
            # 构建 prompt 并调用
            nodes_text = _format_nodes_for_prompt(group)
            prompt = _QA_GENERATION_PROMPT.format(
                group_size=len(group),
                context=nodes_text,
                nodes_text=nodes_text,
            )
            response_text = await _invoke_with_fallback(llms, prompt)

            if response_text:
                qa_list = parse_json_response(response_text)
                for qa in qa_list:
                    record = _qa_to_record(qa, group)
                    if record:
                        all_records.append(record)

            logger.info(
                "[%d/%d] 生成 Q&A: 累计 %d 条",
                i + 1, len(selected_groups), len(all_records),
            )
        except Exception as e:
            logger.error("[%d/%d] 生成失败: %s", i + 1, len(selected_groups), e)

        # Checkpoint
        if checkpoint_every > 0 and (i + 1) % checkpoint_every == 0:
            _save_checkpoint(all_records, i, ckpt_path)

        # 达到 1.5x 目标就停（质量筛选会砍掉一些）
        if len(all_records) >= testset_size * 1.5:
            break

    elapsed = time.time() - t0
    logger.info("Q&A 生成完成: %d 条 (筛选前), %.1f 秒", len(all_records), elapsed)

    # 删除 checkpoint
    if ckpt_path.exists():
        ckpt_path.unlink()

    # 5. 质量筛选
    filtered = filter_quality(all_records)
    logger.info("质量筛选: %d → %d", len(all_records), len(filtered))

    # 截断到目标数量（按类型均匀截断）
    final_records = _stratified_truncate(filtered, testset_size)

    # 6. 持久化
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    dataset_dir = _DATASETS_DIR / timestamp
    dataset_dir.mkdir(parents=True, exist_ok=True)

    # JSONL
    jsonl_path = dataset_dir / "dataset.jsonl"
    for idx, rec in enumerate(final_records):
        rec["id"] = f"q_{idx + 1:03d}"

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for rec in final_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 统计
    type_counts = {}
    diff_counts = {}
    for rec in final_records:
        qt = rec.get("question_type", "unknown")
        diff = rec.get("difficulty", "unknown")
        type_counts[qt] = type_counts.get(qt, 0) + 1
        diff_counts[diff] = diff_counts.get(diff, 0) + 1

    # meta
    meta = {
        "timestamp": timestamp,
        "testset_size": testset_size,
        "actual_size": len(final_records),
        "source": str(doc_dir) if doc_dir else f"kb:{kb_id}",
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "group_size": group_size,
        "group_overlap": group_overlap,
        "elapsed_seconds": round(elapsed, 1),
        "method": "custom_llm_prompt_v2",
        "type_distribution": type_counts,
        "difficulty_distribution": diff_counts,
        "models_used": [llm.model_name for llm in llms[:3]],
    }
    with open(dataset_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info("数据集已保存: %s (%d 条)", jsonl_path, len(final_records))
    logger.info("类型分布: %s", type_counts)
    logger.info("难度分布: %s", diff_counts)
    return jsonl_path


def _qa_to_record(qa: dict, chunks: list[Document]) -> dict | None:
    """将 LLM 输出的单个 Q&A 对转换为标准记录格式。"""
    question = qa.get("question", "").strip()
    answer = qa.get("answer", "").strip()

    if not question or len(question) < 5 or not answer or len(answer) < 20:
        return None

    question_type = qa.get("question_type", "fact").strip().lower()
    difficulty = qa.get("difficulty", "medium").strip().lower()
    key_phrases = qa.get("key_phrases", [])
    dep_indices = qa.get("dependency_node_indices", [])

    # 校验
    if question_type not in ("fact", "keyword", "vague", "negation"):
        question_type = "fact"
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"

    # 收集依赖节点
    valid_indices = [idx for idx in dep_indices if isinstance(idx, int) and 0 <= idx < len(chunks)]
    if not valid_indices:
        # 无标注依赖 → 用全部节点
        valid_indices = list(range(len(chunks)))

    ref_contexts = [chunks[idx].page_content for idx in valid_indices]

    # 来源文档路径
    doc_paths = set()
    for idx in valid_indices:
        chunk = chunks[idx]
        for key in ("title", "file_name", "source"):
            val = chunk.metadata.get(key, "")
            if val:
                doc_paths.add(val)

    # 检索适合性推断
    retrieval_suitability = _infer_retrieval_suitability(question, question_type, key_phrases)

    return {
        "user_input": question,
        "reference": answer,
        "reference_contexts": ref_contexts,
        "expected_doc_paths": sorted(doc_paths),
        "question_type": question_type,
        "difficulty": difficulty,
        "key_phrases": key_phrases,
        "dependency_count": len(valid_indices),
        "retrieval_suitability": retrieval_suitability,
    }


def _infer_retrieval_suitability(
    question: str, question_type: str, key_phrases: list[str]
) -> str:
    """推断问题的检索适合类型。

    - keyword: 术语明确、简短精准 → 适合关键词检索 (BM25)
    - semantic: 表述完整、语义清晰 → 适合向量检索
    - hybrid: 混合特征
    """
    if question_type == "keyword":
        return "keyword"
    if question_type == "vague":
        return "semantic"
    # fact / negation: 看问题特征
    has_tech_terms = any(
        _HIGH_DENSITY_PATTERN.search(p) for p in key_phrases if isinstance(p, str)
    )
    is_short = len(question) <= 15

    if is_short and has_tech_terms:
        return "keyword"
    if len(question) > 30 and not has_tech_terms:
        return "semantic"
    return "hybrid"


def _stratified_truncate(records: list[dict], target: int) -> list[dict]:
    """按类型均匀截断到目标数量。"""
    if len(records) <= target:
        return records

    # 按类型分组
    type_groups: dict[str, list[dict]] = {}
    for rec in records:
        qt = rec.get("question_type", "fact")
        type_groups.setdefault(qt, []).append(rec)

    # 按比例分配名额
    total = len(records)
    result = []
    remaining = target

    for qt, group in type_groups.items():
        quota = max(1, int(target * len(group) / total))
        quota = min(quota, len(group), remaining)
        result.extend(group[:quota])
        remaining -= quota

    # 补充剩余名额
    if remaining > 0:
        used_ids = {id(r) for r in result}
        for rec in records:
            if id(rec) not in used_ids:
                result.append(rec)
                remaining -= 1
                if remaining <= 0:
                    break

    return result[:target]


def load_dataset(path: str | Path) -> list[dict]:
    """加载 JSONL 格式数据集（兼容多行 JSON 对象）。"""
    path = Path(path)
    records = []
    with open(path, encoding="utf-8") as f:
        cur = ""
        depth = 0
        for line in f:
            cur += line
            depth += line.count("{") - line.count("}")
            if depth == 0 and cur.strip():
                try:
                    records.append(json.loads(cur))
                except json.JSONDecodeError:
                    pass
                cur = ""
        # 处理末尾未闭合的情况
        if cur.strip():
            try:
                records.append(json.loads(cur))
            except json.JSONDecodeError:
                pass
    logger.info("加载数据集: %s (%d 条)", path, len(records))
    return records