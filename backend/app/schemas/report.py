from pydantic import BaseModel


class ReportRead(BaseModel):
    id: int
    title: str
    topic: str = ""
    created_at: str
    summary: str
    content: str = ""

    model_config = {"from_attributes": True}


class ReportGenerateRequest(BaseModel):
    title: str | None = None
    article_ids: list[int] | None = None
    prompt: str | None = None
    # prompt: user instructions for report style/focus
