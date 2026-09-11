# File   : settings.py
# Created: 2026-09-11 16:21:29
# Author : octy Sui

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str
    openai_base_url: str
    model_pricing_file: Path = Path("config/model_pricing.toml")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )
