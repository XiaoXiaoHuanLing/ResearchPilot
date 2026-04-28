"""文件解析器选择模块。

根据文件类型选择LlamaIndex解析器：
- md/markdown → MarkdownNodeParser
- pdf → SimpleDirectoryReader (PyMuPDF)
- docx → SimpleDirectoryReader
- txt → 直接读取文本
"""

import logging
from pathlib import Path

from llama_index.core import Document, SimpleDirectoryReader

from app.core.config import settings

logger = logging.getLogger(__name__)


def parse_file(
    file_path: str,
    file_type: str = "text",
    title: str = "",
    source: str = "",
    metadata: dict | None = None,
) -> list[Document]:
    """根据文件类型选择LlamaIndex解析器

    Args:
        file_path: 文件路径（相对于storage_base_dir或绝对路径）
        file_type: "md" / "pdf" / "txt" / "docx"
        title: 文档标题
        source: 来源描述
        metadata: 额外元数据

    Returns:
        LlamaIndex Document 列表
    """
    full_path = _resolve_path(file_path)

    if not full_path.exists():
        logger.error("File not found: %s", file_path)
        return []

    doc_metadata = {
        "title": title or full_path.stem,
        "source": source,
        "file_type": file_type,
        **(metadata or {}),
    }

    if file_type in ("md", "markdown"):
        return _parse_markdown(full_path, doc_metadata)

    elif file_type == "pdf":
        return _parse_pdf(full_path, doc_metadata)

    elif file_type == "docx":
        return _parse_docx(full_path, doc_metadata)

    else:  # txt 等
        return _parse_text(full_path, doc_metadata)


def _resolve_path(file_path: str) -> Path:
    """解析文件路径（相对→绝对）"""
    p = Path(file_path)
    if p.is_absolute() and p.exists():
        return p

    base = Path(settings.storage_base_dir)
    full = base / file_path
    if full.exists():
        return full

    return p  # 返回原始路径（后续检查exists）


def _parse_markdown(file_path: Path, metadata: dict) -> list[Document]:
    """解析Markdown文件"""
    try:
        content = file_path.read_text(encoding="utf-8")
        if not content.strip():
            return []
        return [Document(text=content, metadata=metadata, doc_id=f"doc_{file_path.stem}")]
    except Exception as e:
        logger.error("Markdown parse failed for %s: %s", file_path, e)
        return []


def _parse_pdf(file_path: Path, metadata: dict) -> list[Document]:
    """解析PDF文件"""
    try:
        reader = SimpleDirectoryReader(input_files=[str(file_path)])
        docs = reader.load_data()
        for doc in docs:
            doc.metadata.update(metadata)
        return docs
    except Exception as e:
        logger.error("PDF parse failed for %s: %s", file_path, e)
        # 降级：尝试用pymupdf直接读
        return _parse_pdf_fallback(file_path, metadata)


def _parse_pdf_fallback(file_path: Path, metadata: dict) -> list[Document]:
    """PDF降级解析：直接用PyMuPDF"""
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(file_path))
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()

        if not text.strip():
            return []

        return [Document(text=text, metadata=metadata, doc_id=f"doc_{file_path.stem}")]
    except ImportError:
        logger.warning("PyMuPDF not installed, PDF parsing unavailable")
        return []
    except Exception as e:
        logger.error("PDF fallback parse failed: %s", e)
        return []


def _parse_docx(file_path: Path, metadata: dict) -> list[Document]:
    """解析DOCX文件"""
    try:
        reader = SimpleDirectoryReader(input_files=[str(file_path)])
        docs = reader.load_data()
        for doc in docs:
            doc.metadata.update(metadata)
        return docs
    except Exception as e:
        logger.error("DOCX parse failed for %s: %s", file_path, e)
        # 降级：用python-docx
        return _parse_docx_fallback(file_path, metadata)


def _parse_docx_fallback(file_path: Path, metadata: dict) -> list[Document]:
    """DOCX降级解析：python-docx"""
    try:
        from docx import Document as DocxDocument
        doc = DocxDocument(str(file_path))
        text = "\n".join(para.text for para in doc.paragraphs if para.text.strip())

        if not text.strip():
            return []

        return [Document(text=text, metadata=metadata, doc_id=f"doc_{file_path.stem}")]
    except ImportError:
        logger.warning("python-docx not installed, DOCX parsing unavailable")
        return []
    except Exception as e:
        logger.error("DOCX fallback parse failed: %s", e)
        return []


def _parse_text(file_path: Path, metadata: dict) -> list[Document]:
    """解析纯文本文件"""
    try:
        # 尝试多种编码
        for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
            try:
                content = file_path.read_text(encoding=encoding)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        else:
            content = ""

        if not content.strip():
            return []

        return [Document(text=content, metadata=metadata, doc_id=f"doc_{file_path.stem}")]
    except Exception as e:
        logger.error("Text parse failed for %s: %s", file_path, e)
        return []


def detect_file_type(filename: str) -> str:
    """从文件名检测文件类型"""
    suffix = Path(filename).suffix.lower()
    type_map = {
        ".md": "md", ".markdown": "md",
        ".pdf": "pdf",
        ".docx": "docx", ".doc": "docx",
        ".txt": "txt", ".text": "txt",
        ".csv": "txt",
        ".json": "txt",
    }
    return type_map.get(suffix, "txt")
