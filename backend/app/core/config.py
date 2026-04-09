from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ResearchPilot API"
    app_version: str = "0.1.0"
    database_url: str = f"sqlite:///{(Path(__file__).resolve().parents[2] / 'researchpilot.db').as_posix()}"

    # RAG / LLM settings
    openai_api_key: str = ""
    openai_base_url: str = ""
    embedding_model: str = "text-embedding-3-small"
    llm_model: str = "gpt-4o-mini"

    model_config = SettingsConfigDict(
        env_prefix="RESEARCHPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
