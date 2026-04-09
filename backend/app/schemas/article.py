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

    model_config = {"from_attributes": True}


class ArticleBookmarkUpdate(BaseModel):
    bookmarked: bool
