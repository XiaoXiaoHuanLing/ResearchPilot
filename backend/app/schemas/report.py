from pydantic import BaseModel


class ReportRead(BaseModel):
    id: int
    title: str
    topic: str = ""
    file_path: str = ""
    article_ids_json: str = "[]"
    quality_score: float = 0.0
    quality_detail: str = ""
    status: str = "draft"
    indexed: bool = False
    kb_id: int | None = None
    created_at: str = ""
    updated_at: str = ""

    model_config = {"from_attributes": True}


class ReportGenerateRequest(BaseModel):
    title: str | None = None
    article_ids: list[int] | None = None
    prompt: str | None = None


class OutlineConfirmRequest(BaseModel):
    report_id: int
    action: str = "confirm"  # "confirm" / "edit_confirm" / "regenerate"
    outline: str | None = None
    regenerate_hint: str | None = None
    # prompt: user instructions for report style/focus
