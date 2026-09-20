"""
RAG tool — similarity search against the pgvector knowledge base.

Chunks are embedded via AWS Bedrock (Titan Embed v2 by default) and stored in
Postgres with the pgvector extension. Both the embedding model and table name
are controlled by settings (KB_EMBEDDING_MODEL_ID, KB_TABLE_NAME, KB_TOP_K).

The vectorstore is initialised lazily on the first tool call and then cached
for the lifetime of the process — no reconnect overhead per call.
"""
import asyncio
import json
import logging
from functools import lru_cache

from langchain_aws import BedrockEmbeddings
from langchain_postgres import PGVector

from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.services.llm_service import FunctionCallParams

from backend.src.config.aws import AWSConnectionManager
from backend.src.config.settings import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_vectorstore() -> PGVector:
    """Lazily build and cache the PGVector store for the process lifetime.

    Uses the pre-warmed Bedrock runtime client from AWSConnectionManager so the
    RAG tool shares the same connection pool as the LLM service.
    """
    embeddings = BedrockEmbeddings(
        client=AWSConnectionManager.get_bedrock_runtime_client(),
        model_id=settings.kb_embedding_model_id,
    )
    return PGVector(
        embeddings=embeddings,
        collection_name=settings.kb_table_name,
        connection=settings.pgvector_url,   # psycopg3 URL, not asyncpg
        use_jsonb=True,
    )


@tool_options(cancel_on_interruption=True)
async def query_knowledge_base(params: FunctionCallParams, query: str):
    """Search the knowledge base to answer a user's question.

    Use this tool whenever the user asks a question that requires looking up
    specific information from the organisation's knowledge base — product
    details, policies, FAQs, pricing, procedures, etc.

    Pass the user's question (or a concise, search-friendly rephrasing of it)
    as the query. Prefer noun phrases over full sentences for better retrieval.

    Do NOT call this tool for:
    - General chitchat or greetings
    - Questions you can already answer confidently from conversation context
    - Temporal queries (use get_current_datetime instead)
    - End-of-call handling (use dynamic_end_call instead)

    Args:
        query: The user's question or topic to search for in the knowledge base.
    """
    try:
        vectorstore = _get_vectorstore()

        # similarity_search is synchronous (psycopg3 connection) — run it in
        # a thread executor so it doesn't block the pipecat asyncio event loop.
        loop = asyncio.get_event_loop()
        docs = await loop.run_in_executor(
            None,
            lambda: vectorstore.similarity_search(query, k=settings.kb_top_k),
        )

        if not docs:
            await params.result_callback(json.dumps({
                "found": False,
                "message": "No relevant information found in the knowledge base.",
            }))
            return

        passages = [
            {
                "content": doc.page_content,
                "source": doc.metadata.get("source", "knowledge base"),
            }
            for doc in docs
        ]

        logger.info(f"[rag] query='{query}' returned {len(passages)} passage(s)")

        await params.result_callback(json.dumps({
            "found": True,
            "passages": passages,
        }))

    except Exception as exc:
        logger.exception(f"[rag] query_knowledge_base failed ({type(exc).__name__}) for query='{query}'")
        await params.result_callback(json.dumps({
            "found": False,
            "error": (
                "The knowledge base lookup failed unexpectedly. "
                "Answer based on what you know, or tell the user you cannot confirm that right now."
            ),
        }))