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


try:
    settings = Settings()  # type: ignore[call-arg]
except ValidationError as exc:
    raise RuntimeError(
        "Missing required database environment. Set DATABASE_URL, "
        "DATABASE_URL_SYNC, and POSTGRES_PASSWORD."
    ) from exc
