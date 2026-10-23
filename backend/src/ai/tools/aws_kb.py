"""
RAG tool — retrieval against a managed Amazon Bedrock Knowledge Base.

Drop-in alternative to the pgvector tool in retrieval.py: same tool name
(query_knowledge_base), same LLM-facing description, same result shape, same
spoken acknowledgement — so the system prompt and the rest of the pipeline
don't change when you switch.

HOW TO USE IT (config only — the pipeline uses the pgvector tool by default):
    1. Create a Knowledge Base in the Bedrock console (or via IaC), attach your
       data source(s) (S3, web crawler, Confluence, SharePoint, ...) and run a sync.
       Ingestion is handled by Bedrock — helpers/ingest.py is NOT used here.
    2. Set in backend/.env:
           KB_PROVIDER=aws_kb
           AWS_KB_ID=<your knowledge base id>
       Optional:
           KB_TOP_K=4                    # chunks returned per query (shared with pgvector)
           AWS_KB_SEARCH_TYPE=HYBRID     # or SEMANTIC; unset lets Bedrock decide
           AWS_KB_DATA_SOURCE_IDS=ID1,ID2  # restrict to these data sources; unset searches all
    3. Restart the backend. pipeline.py picks the tool from KB_PROVIDER; the app
       refuses to start if KB_PROVIDER=aws_kb is set without AWS_KB_ID.

A data source ID is NOT required — Retrieve searches every data source in the
KB. It is only needed to sync (StartIngestionJob) or to narrow the search.

The KB is queried with the shared AWS session (AWS_REGION + credentials), so the
KB must live in that region and the credentials need bedrock:Retrieve on it.
Note: AWS_* variables exported in your shell override backend/.env.

The bedrock-agent-runtime client is created lazily on the first tool call and
cached for the lifetime of the process by AWSConnectionManager.
"""
import asyncio
import json
import logging

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


def _retrieve(query: str) -> list[dict]:
    """Run the Bedrock Retrieve API and return its raw retrievalResults."""
    if not settings.aws_kb_id:
        raise ValueError("AWS_KB_ID is not set in backend/.env")

    vector_search_config: dict = {"numberOfResults": settings.kb_top_k}
    if settings.aws_kb_search_type:
        vector_search_config["overrideSearchType"] = settings.aws_kb_search_type
    if settings.aws_kb_data_source_ids:
        # Bedrock tags every chunk with its data source under this built-in metadata key
        data_source_ids = [ds.strip() for ds in settings.aws_kb_data_source_ids.split(",") if ds.strip()]
        vector_search_config["filter"] = {
            "in": {"key": "x-amz-bedrock-kb-data-source-id", "value": data_source_ids},
        }

    response = AWSConnectionManager.get_bedrock_agent_runtime_client().retrieve(
        knowledgeBaseId=settings.aws_kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": vector_search_config},
    )
    return response.get("retrievalResults", [])


def _source_of(result: dict) -> str:
    """Best-effort human-readable source for a retrieval result.

    Each data-source type nests its location under a different key
    (s3Location.uri, webLocation.url, customDocumentLocation.id, ...), so take
    the first identifier found rather than special-casing every type.
    """
    for value in result.get("location", {}).values():
        if isinstance(value, dict):
            for key in ("uri", "url", "id"):
                if value.get(key):
                    return value[key]
    return result.get("metadata", {}).get("x-amz-bedrock-kb-source-uri", "knowledge base")


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

    logger.info(f"query_knowledge_base (aws kb) tool with query: {query}")
    try:
        # boto3 is synchronous — run Retrieve in a thread executor so it doesn't
        # block the pipecat asyncio event loop. Started before the
        # acknowledgement so the two overlap: the turn waits for whichever is
        # slower, not both back to back.
        loop = asyncio.get_running_loop()
        search = loop.run_in_executor(None, lambda: _retrieve(query))

        await _speak_acknowledgement(params, query)
        results = await search

        # non-text results (structured rows, images) can't be read out on a call
        passages = [
            {
                "content": result["content"]["text"],
                "source": _source_of(result),
            }
            for result in results
            if result.get("content", {}).get("text")
        ]

        if not passages:
            await params.result_callback(json.dumps({
                "found": False,
                "message": "No relevant information found in the knowledge base.",
            }))
            return

        logger.info(f"[rag] aws kb query='{query}' returned {len(passages)} passage(s)")

        await params.result_callback(json.dumps({
            "found": True,
            "passages": passages,
        }))

    except Exception as exc:
        logger.exception(f"[rag] aws kb query_knowledge_base failed ({type(exc).__name__}) for query='{query}'")
        await params.result_callback(json.dumps({
            "found": False,
            "error": (
                "The knowledge base lookup failed unexpectedly. "
                "Answer based on what you know, or tell the user you cannot confirm that right now."
            ),
        }))
