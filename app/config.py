from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash-exp"
    max_file_mb: int = 50
    enable_docs: bool = False
    allow_origins: str = "*"


settings = Settings()
