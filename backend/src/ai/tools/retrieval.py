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
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams

from backend.src.config.aws import AWSConnectionManager
from backend.src.config.settings import settings

logger = logging.getLogger(__name__)

# cap on the acknowledgement inference — the answer can't start until the
# acknowledgement is pushed, so a hung Bedrock call must not stall the turn
_ACK_TIMEOUT_SECS = 3.0

_ACK_SYSTEM_INSTRUCTION = """
Generate a brief, natural spoken acknowledgment to let the caller know you are looking up their request.
Sound like a warm, attentive human support representative.

Good examples:
- "Sure thing, let me find that for you."
- "I'd be glad to look that up."
- "Sure, looking into that for you now."
- "One moment, let me check that."

Rules:
- Use 3 to 7 words only.
- Do not answer the question or ask a question.
- Do not mention tools, databases, or systems.
- Return ONLY the spoken words with no quotes.
"""


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


async def _speak_acknowledgement(params: FunctionCallParams, query: str) -> None:
    """Generate a short spoken acknowledgement and push it ahead of the tool result.

    Pushed downstream from the LLM before result_callback, so TTS is guaranteed
    to speak it before the answer generated from this tool's result. Failures
    are swallowed — a missing acknowledgement must never block the lookup.
    """
    ack_context = LLMContext(messages=[
        {"role": "user", "content": f"The caller asked about: {query}"},
    ])
    try:
        acknowledgement = await asyncio.wait_for(
            params.llm.run_inference(
                ack_context,
                max_tokens=30,
                system_instruction=_ACK_SYSTEM_INSTRUCTION,
            ),
            timeout=_ACK_TIMEOUT_SECS,
        )
    except Exception as exc:
        logger.warning(f"[rag] acknowledgement skipped ({type(exc).__name__}) for query='{query}'")
        return

    if acknowledgement:
        await params.llm.push_frame(TTSSpeakFrame(acknowledgement.strip(), append_to_context=False))


@tool_options(cancel_on_interruption=True)
async def query_knowledge_base(params: FunctionCallParams, query: str):
    """Performs semantic vector search against the organization's knowledge base.

    Use this tool whenever the caller asks for company-specific, product-specific,
    or policy-related facts, including:
    - Products, features, tier comparisons, and technical specifications.
    - Pricing, billing terms, discounts, and payment methods.
    - Company policies, refunds, cancellations, warranties, and guarantees.
    - Operating hours, office addresses, contact channels, and support procedures.

    QUERY FORMULATION RULES:
    1. Resolve Pronouns & Context: Substitute vague pronouns ("it", "that", "they") with the
       specific subject discussed earlier (e.g., "enterprise plan storage limit" instead
       of "how much storage does it have").
    2. Use Keyword-Dense Phrases: Strip conversational pleasantries and filler (e.g., turn
       "Could you please tell me if you guys have an SLA?" into "service level agreement SLA terms").
    3. Be Specific: Prefer focused topics over broad generic searches for higher similarity accuracy.

    DO NOT call this tool for:
    - Casual conversation, pleasantries, or acknowledgements ("hello", "thanks", "sounds good").
    - Current date, time, or scheduling questions (use get_current_datetime instead).
    - Concluding or disconnecting the conversation (use end_call instead).
    - Information the caller already stated or that was already established in the active session.

    Args:
        query: Concise, search-optimized search phrase or question targeting the specific information needed.
    """

    logger.info(f"query_knowledge_base tool with query: {query}")
    try:
        # similarity_search is synchronous (psycopg3 connection) — run it in
        # a thread executor so it doesn't block the pipecat asyncio event loop.
        # Started before the acknowledgement so the two overlap: the turn waits
        # for whichever is slower, not both back to back.
        loop = asyncio.get_running_loop()
        search = loop.run_in_executor(
            None,
            lambda: _get_vectorstore().similarity_search(query, k=settings.kb_top_k),
        )

        await _speak_acknowledgement(params, query)
        docs = await search

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