""" central settings file , where all the adjustable modules of the application is present """ 

from functools import lru_cache
from typing import Optional
from pydantic import Field, SecretStr , AliasChoices , ValidationInfo , field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    webhook_url: str = Field(
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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Singleton instance for direct import across non-route modules
settings = get_settings()