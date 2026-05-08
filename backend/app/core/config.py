from pathlib import Path
from typing import Optional

from pydantic import Field, AliasChoices
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
    # Override embedding base_url/api_key for local models (e.g. ollama)
    embedding_base_url_override: str = Field(
        "", validation_alias=AliasChoices("embedding_base_url_override", "EMBEDDING_BASE_URL")
    )
    embedding_api_key_override: str = Field(
        "", validation_alias=AliasChoices("embedding_api_key_override", "EMBEDDING_API_KEY")
    )

    # ─── 咨询过期配置 ───
    article_expire_days: int = 7  # 未收藏资讯过期天数，0=永不过期

    # ─── 报告配置 ───
    report_storage_dir: str = str(Path(__file__).resolve().parents[2] / "storage" / "reports")
    # Eval LLM: 字段名 eval_model_name，支持 .env 变量 EVAL_LLM_MODEL（兼容旧名）
    eval_model_name: str = Field(
        "", validation_alias=AliasChoices("eval_model_name", "EVAL_LLM_MODEL")
    )
    eval_model_base_url: str = Field(
        "", validation_alias=AliasChoices("eval_model_base_url", "EVAL_LLM_BASE_URL")
    )
    eval_model_api_key: str = Field(
        "", validation_alias=AliasChoices("eval_model_api_key", "EVAL_LLM_API_KEY")
    )

    # ─── 存储配置 ───
    storage_base_dir: str = str(Path(__file__).resolve().parents[2] / "storage")

    # ChromaDB persist directory
    chroma_persist_dir: str = str(Path(__file__).resolve().parents[2] / "chroma_db")

    # Search API keys
    tavily_api_key: str = ""
    serper_api_key: str = ""

    _BACKEND_DIR = str(Path(__file__).resolve().parents[2])

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[2] / ".env"),
        env_file_encoding="utf-8-sig",
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
        return self.embedding_api_key_override or self.dashscope_api_key

    @property
    def embedding_base_url(self) -> str:
        """Base URL for embedding model."""
        return self.embedding_base_url_override or self.dashscope_base_url

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

    # ─── Eval LLM resolved properties ───

    @property
    def eval_llm_model(self) -> str:
        """Resolved eval model: eval_model_name > DashScope model > default."""
        if self.eval_model_name:
            return self.eval_model_name
        return self.dashscope_model_name or "qwen3.5-35b-a3b"

    @property
    def eval_llm_base_url(self) -> str:
        """Resolved eval base URL: eval_model_base_url > DashScope URL."""
        if self.eval_model_base_url:
            return self.eval_model_base_url
        return self.dashscope_base_url

    @property
    def eval_llm_api_key(self) -> str:
        """Resolved eval API key: eval_model_api_key > DashScope key."""
        if self.eval_model_api_key:
            return self.eval_model_api_key
        return self.dashscope_api_key


settings = Settings()
