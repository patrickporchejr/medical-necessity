from datetime import date
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: Literal["anthropic", "gemini"] = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"

    # Observability (LangSmith). Without a key nothing is sent anywhere. Runs carry metadata
    # only (counts, ids, models, tokens) unless LANGSMITH_CAPTURE_CONTENT is on.
    langsmith_api_key: str = ""
    langsmith_project: str = "medical-necessity"
    langsmith_endpoint: str = ""  # blank is LangSmith cloud; set for EU or self-hosted
    langsmith_capture_content: bool = False

    # <repo>/data locally, /data in the containers (compose mounts ./data there).
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8001

    # The date "now" means when judging durations such as "at least 90 days of methotrexate".
    # Pin it (AS_OF_DATE=YYYY-MM-DD) to make a run, or an eval, reproducible.
    as_of_date: date | None = None

    @property
    def as_of(self) -> date:
        return self.as_of_date or date.today()

    @property
    def fhir_dir(self) -> Path:
        return self.data_dir / "synthea" / "fhir"

    @property
    def criteria_dir(self) -> Path:
        return self.data_dir / "payer_criteria"


settings = Settings()
