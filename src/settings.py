from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import ProxySettings
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int

    raw_source_database: str = "raw_source"
    knowledge_base_database: str = "knowledge_base"

    proxy_dns: str | None = None
    proxy_port: int | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None

    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"

    bot_token: str | None = None

    @property
    def raw_source_database_url(self) -> str:
        return self.build_database_url(self.raw_source_database)

    @property
    def knowledge_base_database_url(self) -> str:
        return self.build_database_url(self.knowledge_base_database)

    def build_database_url(self, database_name: str) -> str:
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql+psycopg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database_name}"
        )

    def playwright_proxy(self) -> ProxySettings | None:
        if (
            not self.proxy_dns
            or self.proxy_port is None
            or not self.proxy_username
            or not self.proxy_password
        ):
            return None

        return {
            "server": f"http://{self.proxy_dns}:{self.proxy_port}",
            "username": self.proxy_username,
            "password": self.proxy_password,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
