import os
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from sentence_transformers import CrossEncoder

# Load environment variables
load_dotenv()

# -----------------------------
# Azure OpenAI Configuration
# -----------------------------
llm = AzureChatOpenAI(
    azure_deployment=os.getenv(
        "AZURE_LLM_DEPLOYMENT",
        "Llama-4-Maverick-17B-128E-Instruct-FP8"
    ),
    openai_api_version=os.getenv(
        "AZURE_OPENAI_API_VERSION",
        "2024-02-15-preview"
    ),
    api_key=os.getenv("MY_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    temperature=0.1,
)

# -----------------------------
# Directory Configuration
# -----------------------------
PDF_FOLDER = "data"
CHROMA_DIR = "chroma_db"
BM25_CACHE_DIR = "bm25_cache"
DEBUG_TXT_OUTPUT = "debug_chunks_inspected.txt"

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# -----------------------------
# Retrieval Settings
# -----------------------------
TOP_K_PER_QUERY = 15
FINAL_DOCS_PER_QUERY = 6
MAX_TOTAL_CONTEXT_DOCS = 18

# Create required directories
os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs(BM25_CACHE_DIR, exist_ok=True)

# -----------------------------
# Cross Encoder Reranker
# -----------------------------
print("[Setup] Loading CrossEncoder reranker...")
reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

# -----------------------------
# Qdrant Configuration
# -----------------------------
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION_NAME = os.getenv(
    "QDRANT_COLLECTION_NAME",
    "audito_documents"
)