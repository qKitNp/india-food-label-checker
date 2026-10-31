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

    embeddings_enabled: bool = True  # false skips loading the model (tests, quick local runs)
    embedding_model: str = "google/embeddinggemma-2"
    embedding_device: str = "cpu"  # "mps" on a Mac
    embedding_image_tokens: int = 140
    image_weight: float = 0.5  # photo scans: share of the score from the pack image vs the name
    appwrite_images_bucket: str = "pack-images"
    agent_max_steps: int = 14
    agent_timeout: float = 150  # scans run as background jobs, so this isn't bound by proxy timeouts

    # Leave appwrite_project_id empty to fall back to the in-memory store.
    appwrite_endpoint: str = "https://cloud.appwrite.io/v1"
    appwrite_project_id: str = ""
    appwrite_api_key: str = ""
    appwrite_database_id: str = "better_fssai"
    appwrite_products_table: str = "products"
    appwrite_counterparts_table: str = "counterparts"
    appwrite_scan_logs_table: str = "scan_logs"
    appwrite_substances_table: str = "substances"
    appwrite_submissions_table: str = "submissions"
    appwrite_submissions_bucket: str = "pack-images"  # free plan: one bucket for all photos
    # Live voice (Bolna Web Call SDK): the browser mints a call session through us
    # so the Bolna API key never reaches the client.
    bolna_api_key: str = ""
    bolna_agent_id: str = "d09afdca-5794-4127-b2b5-7cf243ab361f"
    # Live captions: a fast audio model, ~2 s per utterance.
    caption_model: str = "gemini-flash-lite-latest"
    caption_timeout: float = 8
    research_daily_cap: int = 100  # codes researched automatically per day

    off_user_agent: str = "better-fssai/0.1 (hackathon project)"


@lru_cache
def get_settings() -> Settings:
    return Settings()
