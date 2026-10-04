"""
Application Configuration Module for Signalpost.
Loads settings from environment variables and .env file.
"""
from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Server configuration
    ENV: str = "development"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DATABASE_PATH: str = "signalpost.db"

    # LLM configuration
    # Supported: "mock", "openai", "gemini", "groq", "openrouter", "ollama"
    LLM_PROVIDER: str = "mock"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_TEMPERATURE: float = 0.0

    # Search provider configuration
    # Supported: "mock", "duckduckgo", "tavily", "serpapi"
    SEARCH_PROVIDER: str = "mock"
    SEARCH_API_KEY: str = ""

    # Official Norwegian Register API (Enhetsregisteret)
    BRREG_API_BASE_URL: str = "https://data.brreg.no/enhetsregisteret/api"
    HTTP_TIMEOUT_SECONDS: int = 15

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def is_testing(self) -> bool:
        return self.ENV.lower() in ("test", "testing")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
