import os
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from sentence_transformers import CrossEncoder

load_dotenv()

# --- LLM INITIALIZATION ---
# Use AzureChatOpenAI for Azure-hosted models
llm = AzureChatOpenAI(
    azure_deployment="Llama-4-Maverick-17B-128E-Instruct-FP8",
    openai_api_version="2024-02-15-preview",
    api_key=os.getenv("MY_API_KEY"),
    azure_endpoint="https://nyailegalai.services.ai.azure.com/",
    temperature=0.1
)

# --- DIRECTORY CONFIGURATION ---
PDF_FOLDER = "data"
CHROMA_DIR = "./chroma_db"
BM25_CACHE_DIR = "./bm25_cache"
DEBUG_TXT_OUTPUT = "debug_chunks_inspected.txt"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

# --- RETRIEVAL TUNING ---
TOP_K_PER_QUERY = 15
FINAL_DOCS_PER_QUERY = 5
MAX_TOTAL_CONTEXT_DOCS = 15

os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs(BM25_CACHE_DIR, exist_ok=True)

print("[Setup] Loading high-precision cross-encoder reranker model...")
reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

# --- QDRANT VECTOR DB ---
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "audito_documents")
