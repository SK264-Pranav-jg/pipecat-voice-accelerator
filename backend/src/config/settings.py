""" central settings file , where all the adjustable modules of the application is present """

from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic import Field, SecretStr , AliasChoices , ValidationInfo , field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to backend/.env regardless of the process's current working directory —
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    
    # basic app settings 
    host : str = Field(
        default="127.0.0.1", description="Host for the fastapi server" 
    )
    port : int = Field(
        default=8080 , description="Port for running the application"
    )

    # Voice Model API Keys
    elevenlabs_api_key: Optional[SecretStr] = Field(
        default=None, description="API key for ElevenLabs voice services"
    )
    sarvam_api_key: Optional[SecretStr] = Field(
        default=None, description="API key for Sarvam AI services"
    )
    cartesia_api_key: Optional[SecretStr] = Field(
        default=None, description="API key for Cartesia voice services"
    )
    cartesia_voice_id: Optional[str] = Field(
        default=None, description="Default Cartesia Voice ID"
    )
    deepgram_api_key : Optional[SecretStr] = Field(
        default=None, description="API for deepgram voice services"
    )

    # Voice ID
    elevenlabs_voice_id: Optional[str] = Field(
        default=None, description="Default ElevenLabs Voice ID"
    )

    # Vobiz Credentials
    vobiz_auth_id: str = Field(
        default="", description="Vobiz authentication ID"
    )
    vobiz_auth_token: SecretStr = Field(
        default=SecretStr(""), description="Vobiz authentication token"
    )
    vobiz_phone_number: str = Field(
        default="", description="Vobiz assigned phone number"
    )
    vobiz_encoding: str = Field(
        default="audio/x-mulaw", description="Audio encoding format for Vobiz (e.g., audio/x-mulaw)"
    )
    vobiz_sample_rate: int = Field(
        default=8000, description="Audio sample rate in Hz for Vobiz XML content-type"
    )
    webhook_endpoint: str = Field(
        default="", description="Webhook URL for event callbacks"
    )

    # AWS Credentials
    aws_session_token: Optional[SecretStr] = Field(
        default=None, description="AWS temporary session token"
    )
    aws_secret_access_key: Optional[SecretStr] = Field(
        default=None, description="AWS secret access key"
    )
    aws_access_key_id: Optional[str] = Field(
        default=None, description="AWS access key ID"
    )
    aws_region: Optional[str] = Field(
        default=None, description="AWS region deployment zone (e.g., us-east-1)"
    )

    # Provider Selection
    stt_provider: str = Field(
        default="elevenlabs", description="Selected Speech-to-Text provider"
    )
    tts_provider: str = Field(
        default="elevenlabs", description="Selected Text-to-Speech provider"
    )

    # Model IDs
    agent_model_id: Optional[str] = Field(
        default=None,
        validation_alias="MAIN_MODEL_ID",
        description="Main AI agent model identifier (mapped from MAIN_MODEL_ID env variable)"
    )

    # Database (Postgres via SQLAlchemy async + asyncpg)
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:1234@localhost:5432/pipecat-accelerator",
        validation_alias=AliasChoices("database_url", "postgres_url"),
        description="Postgres connection URL. Accepts DATABASE_URL or POSTGRES_URL; a plain "
                     "postgresql:// scheme is upgraded to the asyncpg driver automatically."
    )
    db_pool_size: int = Field(
        default=5, description="Base connection pool size for the Postgres engine"
    )
    db_max_overflow: int = Field(
        default=10, description="Max connections allowed beyond db_pool_size under load"
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_postgres_scheme(cls, value: str) -> str:
        """Accept a plain postgresql:// (or Heroku-style postgres://) URL and upgrade
        it to the asyncpg driver scheme SQLAlchemy's async engine requires."""
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Singleton instance for direct import across non-route modules
settings = get_settings()