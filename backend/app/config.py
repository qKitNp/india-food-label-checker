from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LiteLLM model names: "<provider>/<model>". The model must accept image input.
    llm_model: str = "deepseek/deepseek-flash"
    llm_fallbacks: list[str] = ["gemini/gemini-3.8-flash"]
    llm_timeout: float = 30
    deepseek_api_key: str = ""
    gemini_api_key: str = ""
    exa_api_key: str = ""

    embedding_model: str = "nomic-embed-text"  # Appwrite built-in; switch to embedding-gemma when it ships
    agent_max_steps: int = 8
    agent_timeout: float = 90

    # Leave appwrite_project_id empty to fall back to the in-memory store.
    appwrite_endpoint: str = "https://cloud.appwrite.io/v1"
    appwrite_project_id: str = ""
    appwrite_api_key: str = ""
    appwrite_database_id: str = "better_fssai"
    appwrite_products_table: str = "products"
    appwrite_counterparts_table: str = "counterparts"

    off_user_agent: str = "better-fssai/0.1 (hackathon project)"


@lru_cache
def get_settings() -> Settings:
    return Settings()
