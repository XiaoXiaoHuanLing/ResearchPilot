from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ResearchPilot API"
    app_version: str = "0.2.0"
    database_url: str = f"sqlite:///{(Path(__file__).resolve().parents[2] / 'researchpilot.db').as_posix()}"

    # LLM settings (for chat/generation)
    openai_api_key: str = ""
    openai_base_url: str = ""
    llm_model: str = ""

    # Embedding settings (separate from LLM, may use different provider)
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = ""

    # ChromaDB persist directory
    chroma_persist_dir: str = str(Path(__file__).resolve().parents[2] / "chroma_db")

    # Search API keys
    tavily_api_key: str = ""
    serper_api_key: str = ""

    # Extra .env vars for fallback resolution
    alibaba_base_url: str = ""
    alibaba_api_key: str = ""
    alibaba_model_name: str = ""
    alibaba_model_name_fallback: str = ""
    alibaba_model_embedding_name: str = ""
    alibaba_model_embedding_name_fallback: str = ""
    openai_model_name: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def resolve(self) -> "Settings":
        """Resolve effective configuration with fallback logic."""
        # --- LLM ---
        # API key: openai_api_key > alibaba_api_key
        if not self.openai_api_key and self.alibaba_api_key:
            self.openai_api_key = self.alibaba_api_key

        # Base URL: openai_base_url > alibaba_base_url
        if not self.openai_base_url and self.alibaba_base_url:
            self.openai_base_url = self.alibaba_base_url

        # LLM model: llm_model > openai_model_name > alibaba_model_name > default
        if not self.llm_model:
            if self.openai_model_name:
                self.llm_model = self.openai_model_name
            elif self.alibaba_model_name:
                self.llm_model = self.alibaba_model_name
            else:
                self.llm_model = "gpt-4o-mini"

        # --- Embedding (separate provider support) ---
        # embedding_api_key: explicit > alibaba_api_key (embedding usually on Alibaba)
        if not self.embedding_api_key:
            self.embedding_api_key = self.alibaba_api_key or self.openai_api_key

        # embedding_base_url: explicit > alibaba_base_url (DashScope for Alibaba models)
        if not self.embedding_base_url:
            self.embedding_base_url = self.alibaba_base_url or self.openai_base_url

        # embedding_model: explicit > alibaba_model_embedding_name > default
        if not self.embedding_model:
            if self.alibaba_model_embedding_name:
                self.embedding_model = self.alibaba_model_embedding_name
            else:
                self.embedding_model = "text-embedding-3-small"

        return self


settings = Settings().resolve()
