from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = Field(min_length=1, validation_alias="DATABASE_URL")
    database_url_sync: str = Field(
        min_length=1,
        validation_alias="DATABASE_URL_SYNC",
    )
    postgres_password: str = Field(
        min_length=1,
        validation_alias="POSTGRES_PASSWORD",
    )
    key_lookup_secret: str = Field(
        min_length=1,
        validation_alias="AQ_KEY_LOOKUP_SECRET",
    )
    claim_lease_seconds: int = Field(
        default=900,
        validation_alias="AQ_CLAIM_LEASE_SECONDS",
        ge=60,
        le=86400,
    )
    claim_sweep_interval_seconds: int = Field(
        default=60,
        validation_alias="AQ_CLAIM_SWEEP_INTERVAL_SECONDS",
        ge=5,
        le=3600,
    )


try:
    settings = Settings()  # type: ignore[call-arg]
except ValidationError as exc:
    raise RuntimeError(
        "Missing required database environment. Set DATABASE_URL, "
        "DATABASE_URL_SYNC, POSTGRES_PASSWORD, and AQ_KEY_LOOKUP_SECRET."
    ) from exc
