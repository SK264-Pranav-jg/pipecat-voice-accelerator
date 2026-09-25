"""Centralized AWS session and client connection manager."""

import threading
from functools import lru_cache
from typing import Optional

import boto3
from botocore.config import Config

from backend.src.config.settings import settings


class AWSConnectionManager:
    """Thread-safe singleton manager for AWS Sessions and clients."""

    _session: Optional[boto3.Session] = None
    # avoiding race conditions when two thread look for initiating a new session 
    _lock = threading.Lock()

    @classmethod
    def get_session(cls) -> boto3.Session:
        """Initializes or returns a shared thread-safe boto3 Session."""
        if cls._session is None:
            with cls._lock:
                if cls._session is None:
                    session_kwargs = {}
                    if settings.aws_region:
                        session_kwargs["region_name"] = settings.aws_region
                    if settings.aws_access_key_id and settings.aws_secret_access_key:
                        session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
                        session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key.get_secret_value()
                        if settings.aws_session_token:
                            session_kwargs["aws_session_token"] = settings.aws_session_token.get_secret_value()

                    cls._session = boto3.Session(**session_kwargs)
        return cls._session

    @staticmethod
    def _get_boto_config(max_pool_connections: int = 50, read_timeout: int = 30) -> Config:
        """Returns standard botocore config with connection pooling and adaptive retries."""
        return Config(
            region_name=settings.aws_region or "ap-south-1",
            retries={
                "max_attempts": 3,
                "mode": "adaptive",
            },
            max_pool_connections=max_pool_connections,
            connect_timeout=5,          # Fixed: connect_timeout instead of connection_timeout
            read_timeout=read_timeout,
        )

    # Cached Client Singletons (Reused across all concurrent calls)

    @classmethod
    @lru_cache(maxsize=1)
    def get_s3_client(cls):
        """Client for S3 audio recordings and KB documents."""
        session = cls.get_session()
        return session.client("s3", config=cls._get_boto_config(read_timeout=60))

    @classmethod
    @lru_cache(maxsize=1)
    def get_bedrock_runtime_client(cls):
        """Client for LLM inference (Claude Haiku, summaries)."""
        session = cls.get_session()
        return session.client("bedrock-runtime", config=cls._get_boto_config(read_timeout=20))

    @classmethod
    @lru_cache(maxsize=1)
    def get_bedrock_agent_runtime_client(cls):
        """Client for Bedrock Knowledge Base vector retrieval (RAG)."""
        session = cls.get_session()
        return session.client("bedrock-agent-runtime", config=cls._get_boto_config(read_timeout=30))

    @classmethod
    @lru_cache(maxsize=1)
    def get_bedrock_agent_client(cls):
        """Client for managing KB data sources and ingestion sync jobs."""
        session = cls.get_session()
        return session.client("bedrock-agent", config=cls._get_boto_config(read_timeout=30))


# Module-level Accessor Functions

# client for s3 
def get_s3():
    return AWSConnectionManager.get_s3_client()


# client for llm models via bedrock 
def get_bedrock_runtime():
    return AWSConnectionManager.get_bedrock_runtime_client()

# client for knowledge bases and retrieval 
def get_bedrock_agent_runtime():
    return AWSConnectionManager.get_bedrock_agent_runtime_client()

# client for knowledge bases sync jobs and managing knowledge bases 
def get_bedrock_agent():
    return AWSConnectionManager.get_bedrock_agent_client()