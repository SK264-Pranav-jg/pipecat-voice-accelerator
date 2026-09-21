"""
Standalone document ingestion script.

Loads PDF files from a directory, splits them into chunks, embeds each chunk
via AWS Bedrock, and stores the vectors in Postgres (pgvector).

Run from the project root:
    python -m backend.src.helpers.ingest --docs "path/to/your/docs/"

Optional flags:
    --chunk-size     Token target per chunk (default 800)
    --chunk-overlap  Overlap between consecutive chunks (default 100)
    --clear          Drop and recreate the collection before ingesting

"""
import argparse
import logging
from pathlib import Path

from langchain_aws import BedrockEmbeddings
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.src.config.aws import AWSConnectionManager
from backend.src.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _build_vectorstore(pre_delete_collection: bool = False) -> PGVector:
    """Build a PGVector store using the app's Bedrock client and settings."""
    embeddings = BedrockEmbeddings(
        client=AWSConnectionManager.get_bedrock_runtime_client(),
        model_id=settings.kb_embedding_model_id,
    )
    return PGVector(
        embeddings=embeddings,
        collection_name=settings.kb_table_name,
        connection=settings.pgvector_url,   # psycopg3 URL (not asyncpg)
        use_jsonb=True,
        pre_delete_collection=pre_delete_collection,
    )


def _load_documents(path: Path) -> list:
    """Load from a single PDF file or a directory of PDFs."""
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Only PDF files are supported, got: {path.suffix}")
        logger.info(f"Loading single file: {path}")
        return PyPDFLoader(str(path)).load()

    # Directory — walk recursively for all PDFs
    logger.info(f"Loading PDFs from directory: {path}")
    loader = DirectoryLoader(
        str(path),
        glob="**/*.pdf",
        loader_cls=PyPDFLoader,
        show_progress=True,
        use_multithreading=True,
    )
    return loader.load()


def ingest(
    docs_path: str,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
    clear: bool = False,
) -> None:
    target = Path(docs_path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {target}")

    # ── 1. Load ──────────────────────────────────────────────────────────────
    raw_docs = _load_documents(target)

    if not raw_docs:
        logger.warning("No content loaded. Check the path and make sure PDFs are readable.")
        return

    logger.info(f"Loaded {len(raw_docs)} page(s)")

    # ── 2. Split ──────────────────────────────────────────────────────────────
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_documents(raw_docs)
    logger.info(
        f"Split into {len(chunks)} chunk(s)  "
        f"(chunk_size={chunk_size}, overlap={chunk_overlap})"
    )

    # ── 3. Embed + store ──────────────────────────────────────────────────────
    if clear:
        logger.warning(
            f"--clear flag set: dropping and recreating collection '{settings.kb_table_name}'"
        )

    logger.info(
        f"Embedding {len(chunks)} chunks with '{settings.kb_embedding_model_id}' "
        f"and storing in table '{settings.kb_table_name}' ..."
    )

    vectorstore = _build_vectorstore(pre_delete_collection=clear)
    vectorstore.add_documents(chunks)

    logger.info(f"Done. {len(chunks)} chunks stored in '{settings.kb_table_name}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest PDF documents into the pgvector knowledge base.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--docs",
        required=True,
        metavar="PATH",
        help="Path to a single PDF file OR a directory of PDFs (searched recursively).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=800,
        metavar="N",
        help="Target character count per chunk.",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=100,
        metavar="N",
        help="Character overlap between consecutive chunks.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Drop the existing collection and start fresh before ingesting.",
    )
    args = parser.parse_args()

    ingest(
        docs_path=args.docs,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        clear=args.clear,
    )