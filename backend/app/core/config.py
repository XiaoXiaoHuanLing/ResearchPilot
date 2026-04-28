from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ResearchPilot API"
    app_version: str = "0.3.0"
    database_url: str = f"sqlite:///{(Path(__file__).resolve().parents[2] / 'researchpilot.db').as_posix()}"

    # ─── LLM settings (Alibaba DashScope — OpenAI-compatible API) ───
    # Used for all chat/generation tasks: Copilot, QA, Report, HyDE, Supervisor
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model_name: str = ""  # e.g. "qwen3.5-plus"
    dashscope_model_name_fallback: str = ""  # Fallback 1
    dashscope_model_name_fallback_2: str = ""  # Fallback 2
    dashscope_model_name_fallback_3: str = ""  # Fallback 3
    dashscope_model_name_fallback_4: str = ""  # Fallback 4

    # ─── Embedding settings (same DashScope endpoint, separate model) ───
    dashscope_model_embedding_name: str = ""
    dashscope_model_embedding_name_fallback: str = ""

    # ─── 咨询过期配置 ───
    article_expire_days: int = 7  # 未收藏资讯过期天数，0=永不过期

    # ─── 报告配置 ───
    report_storage_dir: str = str(Path(__file__).resolve().parents[2] / "storage" / "reports")
    eval_model_name: str = ""   # 空=用主模型，非空=指定评估模型

    # ─── 存储配置 ───
    storage_base_dir: str = str(Path(__file__).resolve().parents[2] / "storage")

    # ChromaDB persist directory
    chroma_persist_dir: str = str(Path(__file__).resolve().parents[2] / "chroma_db")

    # Search API keys
    tavily_api_key: str = ""
    serper_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ─── Derived properties ───

    @property
    def llm_model(self) -> str:
        """Resolved effective chat model name."""
        return self.dashscope_model_name or "qwen3.5-35b-a3b"

    @property
    def llm_api_key(self) -> str:
        """API key for chat LLM (same as embedding)."""
        return self.dashscope_api_key

    @property
    def llm_base_url(self) -> str:
        """Base URL for chat LLM (same as embedding)."""
        return self.dashscope_base_url

    @property
    def embedding_api_key(self) -> str:
        """API key for embedding model."""
        return self.dashscope_api_key

    @property
    def embedding_base_url(self) -> str:
        """Base URL for embedding model."""
        return self.dashscope_base_url

    @property
    def embedding_model(self) -> str:
        """Resolved effective embedding model name."""
        return self.dashscope_model_embedding_name or "text-embedding-v3"

    @property
    def llm_configured(self) -> bool:
        """Whether chat LLM is configured."""
        return bool(self.dashscope_api_key)

    @property
    def embedding_configured(self) -> bool:
        """Whether embedding is configured."""
        return bool(self.dashscope_api_key and self.dashscope_model_embedding_name)


settings = Settings()
