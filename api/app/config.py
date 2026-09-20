from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    langsmith_api_key: str = ""

    # <repo>/data locally, /data in the containers (compose mounts ./data there).
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8001

    @property
    def fhir_dir(self) -> Path:
        return self.data_dir / "synthea" / "fhir"

    @property
    def criteria_dir(self) -> Path:
        return self.data_dir / "payer_criteria"


settings = Settings()
