"""Application settings. Every value can be overridden via environment / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "neuroflow"
    postgres_user: str = "neuroflow"
    postgres_password: str  # required, from .env

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str  # required, from .env

    # MLflow
    mlflow_tracking_uri: str = "http://localhost:5000"

    # Observability (OTLP gRPC endpoint of Jaeger)
    otlp_endpoint: str = "http://localhost:4317"
    service_name: str = "neuroflow-api"

    # LLM providers
    llm_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    anthropic_api_key: str = ""

    # Ingestion: where uploaded files are stored (shared between API and worker)
    upload_dir: str = "./uploads"

    # Directory holding the .sql schema files applied by db/migrations.py
    migrations_dir: str = "../infra/init"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
