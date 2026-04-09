from pydantic import BaseModel


class TopicBase(BaseModel):
    name: str
    description: str
    keywords: list[str]
    schedule: str
    enabled: bool = True


class TopicCreate(TopicBase):
    pass


class TopicUpdate(TopicBase):
    pass


class TopicRead(TopicBase):
    id: int

    model_config = {"from_attributes": True}
