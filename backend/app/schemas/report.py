from pydantic import BaseModel


class ReportRead(BaseModel):
    id: int
    title: str
    created_at: str
    summary: str

    model_config = {"from_attributes": True}
