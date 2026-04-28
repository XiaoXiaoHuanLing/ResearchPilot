from pydantic import BaseModel


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: str = ""


class KnowledgeBaseRead(BaseModel):
    id: int
    name: str
    description: str
    kb_type: str
    enabled: bool = True
    document_count: int = 0
    chunk_count: int = 0
    created_at: str = ""
    updated_at: str = ""

    model_config = {"from_attributes": True}


class KbDocumentRead(BaseModel):
    id: int
    kb_id: int
    title: str
    file_path: str = ""
    file_type: str = "text"
    source_type: str = "upload"
    source: str = ""
    index_status: str = "pending"
    chunk_count: int = 0
    created_at: str = ""
    indexed_at: str = ""

    model_config = {"from_attributes": True}


class KbDocumentUpload(BaseModel):
    title: str
    content: str
    file_type: str = "text"
