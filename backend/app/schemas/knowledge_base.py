from pydantic import BaseModel


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: str = ""


class KnowledgeBaseRead(BaseModel):
    id: int
    name: str
    description: str
    kb_type: str
    is_default: bool
    article_count: int
    created_at: str

    model_config = {"from_attributes": True}


class KbDocumentRead(BaseModel):
    id: int
    kb_id: int
    title: str
    source: str
    file_type: str
    indexed: bool
    created_at: str

    model_config = {"from_attributes": True}


class KbDocumentUpload(BaseModel):
    title: str
    content: str
    file_type: str = "text"
