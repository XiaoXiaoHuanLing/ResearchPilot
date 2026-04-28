from pydantic import BaseModel


class ArticleRead(BaseModel):
    id: int
    topic: str
    title: str
    source: str
    published_at: str
    summary: str
    url: str
    bookmarked: bool
    content: str = ""
    quality_score: int = -1
    quality_label: str = ""
    expires_at: str = ""

    model_config = {"from_attributes": True}


class ArticleBookmarkUpdate(BaseModel):
    bookmarked: bool
