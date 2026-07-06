"""
LEGACY PROTOTYPE — kept for reference only.

This is the original single-user, CLI-driven RAG prototype (Chroma + BM25 +
CrossEncoder reranker + entity-aware query routing via LangGraph). It has been
superseded by the production multi-tenant backend in `backend/app/` (FastAPI +
JWT auth + Qdrant + per-user/session BM25 caches). Not wired into the running
service — do not import this module from app/.
"""

import os
import re
import json
import shutil
import pdfplumber
import pytesseract
import concurrent.futures
from collections import Counter
from typing import List, Dict, Any, TypedDict, Annotated
from operator import add
from dotenv import load_dotenv

# LangChain & LangGraph imports
from langchain_core.documents import Document
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.retrievers import BM25Retriever
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

# CrossEncoder imports for accuracy Reranking
from sentence_transformers import CrossEncoder

# Uncomment and set your path if using Windows and Tesseract isn't in your PATH
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

load_dotenv()

# --- CRITICAL ACCURACY LLM UPDATE ---
llm = ChatOpenAI(
    model="Llama-4-Maverick-17B-128E-Instruct-FP8",
    api_key=os.getenv("MY_API_KEY"),
    base_url="https://nyailegalai.services.ai.azure.com/openai/v1/",
    temperature=0.4
)

# --- CONFIGURATION ---
CHROMA_DIR = "./chroma_db"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

print("[Setup] Loading high-precision BGE Reranker model...")
reranker = CrossEncoder("BAAI/bge-reranker-large")


# --- USER INPUT FOR PDFS ---
def get_user_pdfs() -> List[str]:
    """Prompts the user to input one or multiple PDF paths via CLI."""
    print("Please enter the full paths to the PDF files you want to analyze.")
    print("For multiple files, separate the paths with a comma (,).")

    while True:
        paths_input = input("\nPDF Path(s): ")
        if not paths_input.strip():
            print("No input provided. Please try again or type 'exit' to quit.")
            continue

        if paths_input.lower() in ['exit', 'quit']:
            return []

        pdf_files = []
        raw_paths = [p.strip().strip('"').strip("'") for p in paths_input.split(',')]

        for path in raw_paths:
            if not path:
                continue
            if os.path.isfile(path) and path.lower().endswith('.pdf'):
                pdf_files.append(path)
            else:
                print(f"[!] Warning: Invalid file or not a PDF -> Skipped: {path}")

        if pdf_files:
            print(f"\n[Success] Loaded {len(pdf_files)} valid PDF(s).")
            return pdf_files
        else:
            print("[!] No valid PDFs found in your input. Please check the paths and try again.")


def convert_table_to_markdown(table: List[List[Any]]) -> str:
    """Converts a raw pdfplumber table layout safely into a pristine Markdown table with strict newlines."""
    if not table or not any(table):
        return ""

    cleaned_table = [[str(cell).strip().replace("\n", " ") if cell is not None else "" for cell in row] for row in table]
    cleaned_table = [row for row in cleaned_table if any(cell for cell in row)]
    if not cleaned_table:
        return ""

    markdown_output = "\n\n"
    markdown_output += "| " + " | ".join(cleaned_table[0]) + " |\n"
    markdown_output += "| " + " | ".join(["---"] * len(cleaned_table[0])) + " |\n"

    for row in cleaned_table[1:]:
        if len(row) < len(cleaned_table[0]):
            row += [""] * (len(cleaned_table[0]) - len(row))
        markdown_output += "| " + " | ".join(row[:len(cleaned_table[0])]) + " |\n"

    return markdown_output + "\n"


def extract_dominant_entity(text: str) -> str:
    """Generically extracts the dominant entity/subject context from a text block using Regex."""
    candidates = re.findall(r'\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})*\b', text)
    stop_entities = {"Total", "Company", "Financial", "Year", "Ended", "March", "Note", "Notes", "Report", "Limited", "Income", "Balance"}
    filtered = [c for c in candidates if c not in stop_entities]

    if not filtered:
        return "General"
    return Counter(filtered).most_common(1)[0][0]


# --- PARSING ENGINE ---
def process_single_page(pdf_path: str, page_num: int, file_name: str, file_year: str, chunk_size: int = 1500, chunk_overlap: int = 200) -> List[Document]:
    """Isolated worker function to process a single PDF page."""
    chunked_docs = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_num - 1]

            # pdfplumber handles orientation automatically; no manual .rotate() call.
            page_raw_text = page.extract_text() or ""
            page_elements = []

            if len(page_raw_text.strip()) < 50:
                try:
                    # Attempt OCR if text extraction is poor
                    pil_image = page.to_image(resolution=300).original
                    ocr_text = pytesseract.image_to_string(pil_image)
                    if ocr_text.strip():
                        page_elements.append((ocr_text, False))
                        page_entity = extract_dominant_entity(ocr_text)
                    else:
                        page_entity = "General"
                except Exception:
                    page_entity = "General"
            else:
                page_entity = extract_dominant_entity(page_raw_text)

                # Extract tables
                for t in page.find_tables():
                    md_table = convert_table_to_markdown(t.extract())
                    if md_table.strip():
                        page_elements.append((md_table, True))

                # Extract layout text
                layout_text = page.extract_text(layout=True) or ""
                if layout_text.strip():
                    page_elements.append((layout_text, False))

            # Assemble content
            full_page_text = ""
            table_boundaries = []
            for element, is_table in page_elements:
                if not is_table:
                    # Clean lines
                    lines = [l.rstrip() for l in element.splitlines() if len(l.strip()) > 2]
                    element = "\n".join(lines)
                if not element.strip():
                    continue

                start_pos = len(full_page_text)
                full_page_text += "\n\n" + element
                if is_table:
                    table_boundaries.append((start_pos, len(full_page_text)))

            full_page_text = full_page_text.strip()
            if not full_page_text:
                return chunked_docs

            # Chunking logic
            prefix = f"=== FILE: '{file_name}' | CONTEXT SUBJECT: {page_entity} | YEAR: {file_year} | PAGE: {page_num} ===\n"
            start_idx = 0
            while start_idx < len(full_page_text):
                end_idx = min(start_idx + chunk_size, len(full_page_text))

                # Prevent breaking tables
                for t_start, t_end in table_boundaries:
                    if t_start < end_idx < t_end:
                        end_idx = t_end
                        break

                chunk_text = full_page_text[start_idx:end_idx].strip()
                if chunk_text:
                    chunked_docs.append(Document(
                        page_content=prefix + chunk_text,
                        metadata={"source": file_name, "page": page_num, "entity": page_entity, "year": file_year}
                    ))

                start_idx = max(end_idx - chunk_overlap, end_idx)
                if start_idx >= len(full_page_text):
                    break

    except Exception as e:
        print(f" [!] Error processing {file_name} page {page_num}: {e}")

    return chunked_docs


def parse_and_chunk_structural_pdfs(pdf_files: List[str]) -> List[Document]:
    """Parses PDFs sequentially to ensure stability and avoid Windows multiprocessing errors."""
    chunked_documents = []

    for pdf_path in pdf_files:
        file_name = os.path.basename(pdf_path)
        print(f"\n[Parsing] Processing {file_name}...")

        year_match = re.search(r'(20\d{2}-\d{2}|20\d{2})', file_name)
        file_year = year_match.group(1) if year_match else "Unknown"

        try:
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
                print(f" -> Found {total_pages} pages. Starting sequential extraction...")

                for page_num in range(1, total_pages + 1):
                    docs = process_single_page(pdf_path, page_num, file_name, file_year)
                    if docs:
                        chunked_documents.extend(docs)

                    if page_num % 5 == 0 or page_num == total_pages:
                        print(f"    [+] Progress: {page_num}/{total_pages} pages parsed.")

        except Exception as e:
            print(f"[!] Failed to process {file_name}: {e}")

    print(f"\n[Ingestion Finished] Total segments mapped: {len(chunked_documents)}\n")
    return chunked_documents


def load_and_index_directory(pdf_files: List[str] = None):
    """Loads the database from disk if it exists, otherwise builds a new one."""
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

    is_cached = os.path.exists(CHROMA_DIR) and len(os.listdir(CHROMA_DIR)) > 0

    if is_cached and not pdf_files:
        print("\n[Cache] Loading existing structured vector database from disk...")
        vectorstore = Chroma(persist_directory=CHROMA_DIR, embedding_function=embeddings)

        print("[Cache] Rebuilding BM25 sparse index from cached documents...")
        all_data = vectorstore.get()

        cached_docs = [
            Document(page_content=text, metadata=meta)
            for text, meta in zip(all_data['documents'], all_data['metadatas'])
        ]

        bm25_retriever = BM25Retriever.from_documents(cached_docs)
        print(f"[Success] Loaded {len(cached_docs)} segments from local cache.")
        return vectorstore, bm25_retriever

    else:
        if not pdf_files:
            print("[!] No database exists, and no new files were provided.")
            return None, None

        print("\n[New Setup] Starting fresh structural index pipeline...")
        chunks = parse_and_chunk_structural_pdfs(pdf_files)
        if not chunks:
            return None, None

        vectorstore = Chroma.from_documents(chunks, embeddings, persist_directory=CHROMA_DIR)
        bm25_retriever = BM25Retriever.from_documents(chunks)
        print(" [Success] Data parsed, embedded, and saved to disk for future runs.")

        return vectorstore, bm25_retriever


def hybrid_search(query: str, vectorstore, bm25, entity_filter: str = None, top_k: int = 12) -> List[Document]:
    """Runs high-k candidate extraction applying open-domain metadata scopes if identified."""
    kwargs = {}
    if entity_filter and entity_filter != "General":
        kwargs["filter"] = {"entity": entity_filter}

    dense_results = vectorstore.similarity_search(query, k=top_k, **kwargs)
    sparse_results = bm25.invoke(query)[:top_k]

    seen_contents = set()
    combined_results = []
    for doc in (dense_results + sparse_results):
        if entity_filter and entity_filter != "General":
            if doc.metadata.get("entity") != entity_filter and doc.metadata.get("entity") != "General":
                continue
        if doc.page_content not in seen_contents:
            seen_contents.add(doc.page_content)
            combined_results.append(doc)
    return combined_results


# --- LANGGRAPH STATE ENGINE ---
class AgentState(TypedDict):
    query: str
    rewritten_queries: List[str]
    entity_scope: str
    chat_history: Annotated[list, add]
    retrieved_docs: List[Document]
    response: str


def analyze_and_rewrite_node(state: AgentState):
    user_query = state["query"]

    system_prompt = (
        "You are an advanced financial and legal database routing agent.\n"
        "Analyze the user's question and output a strict JSON object containing:\n"
        "1. 'entity': String. Identify if the query singles out an isolated entity, division, project, or subsidiary. "
        "If the question is broad, comparative, or doesn't mention a distinct sub-entity, output 'General'.\n"
        "2. 'sub_queries': A list of strings. If the question asks for comparisons across different periods, multiple years, "
        "historical milestones, or multiple technical components, rewrite the user input into a list of highly focused, localized queries "
        "targeting each individual fact needed. Keep it as a single-item list if the question is straightforward.\n\n"
        "Example Output Format:\n"
        "{\n  \"entity\": \"Segment B\",\n  \"sub_queries\": [\"Segment B performance FY24\", \"Segment B performance FY25\"]\n}"
    )

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_query)]
    res = llm.invoke(messages)

    try:
        structured_routing = json.loads(res.content)
        entity = structured_routing.get("entity", "General")
        sub_queries = structured_routing.get("sub_queries", [user_query])
    except Exception:
        entity = "General"
        sub_queries = [user_query]

    return {"entity_scope": entity, "rewritten_queries": sub_queries}


def retrieve_node(state: AgentState):
    entity = state["entity_scope"]
    sub_queries = state["rewritten_queries"]

    aggregated_candidates = []
    seen_contents = set()

    for q in sub_queries:
        candidates = hybrid_search(q, vectorstore, bm25_retriever, entity_filter=entity, top_k=8)
        for doc in candidates:
            if doc.page_content not in seen_contents:
                seen_contents.add(doc.page_content)
                aggregated_candidates.append(doc)

    if not aggregated_candidates:
        return {"retrieved_docs": []}

    pairs = [[state["query"], doc.page_content] for doc in aggregated_candidates]
    rerank_scores = reranker.predict(pairs)

    scored_docs = sorted(zip(rerank_scores, aggregated_candidates), key=lambda x: x[0], reverse=True)

    RERANK_THRESHOLD = 0.15
    verified_docs = []

    for score, doc in scored_docs[:4]:
        if score >= RERANK_THRESHOLD:
            verified_docs.append(doc)
        else:
            print(f" [Guard] Dropping low-confidence candidate chunk (Score: {score:.4f})")

    if verified_docs:
        print(f" Top Match Verified -> Source: {verified_docs[0].metadata['source']}, Page: {verified_docs[0].metadata['page']}")

    return {"retrieved_docs": verified_docs}


def generate_node(state: AgentState):
    if not state["retrieved_docs"]:
        return {
            "response": "Refused: The requested data metrics or specific entity configurations could not be verified within the context of the provided document data blocks.",
            "chat_history": []
        }

    context_str = "\n\n".join([d.page_content for d in state["retrieved_docs"]])

    system_prompt = (
        "You are an expert precision financial and legal auditor. Your task is to answer the user's question with absolute data integrity.\n"
        "Use ONLY the facts and structural context provided.\n\n"
        "CRITICAL RULE 1: If a metric or number in the text is accompanied by modifiers like 'over', 'more than', 'approx', or 'nearly', "
        "you MUST include that modifier in your final answer.\n"
        "CRITICAL RULE 2: If the context contains a Markdown table or data blocks relevant to the user's question, you MUST present the data "
        "using a clean, standard Markdown table layout.\n"
        "CRITICAL RULE 3: ANTI-HALLUCINATION GUARD. Do not answer anything not provided in the text.\n"
        "CRITICAL RULE 4: At the very end of your response, you MUST generate 2-3 logical follow-up questions that the user could ask "
        "to dive deeper into the provided data. Present them as a bulleted list under the markdown heading '### Suggested Follow-up Questions'."
    )

    user_prompt = f"Context:\n{context_str}\n\nQuestion: {state['query']}"

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    response = llm.invoke(messages)

    return {
        "response": response.content,
        "chat_history": [f"User: {state['query']}", f"Bot: {response.content}"]
    }


# Build workflow Graph
workflow = StateGraph(AgentState)
workflow.add_node("analyze_and_rewrite", analyze_and_rewrite_node)
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("generate", generate_node)

workflow.add_edge(START, "analyze_and_rewrite")
workflow.add_edge("analyze_and_rewrite", "retrieve")
workflow.add_edge("retrieve", "generate")
workflow.add_edge("generate", END)

app = workflow.compile()


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print(" MULTIMODAL HYBRID RAG - SYSTEM BOOT")
    print("=" * 50)

    has_cache = os.path.exists(CHROMA_DIR) and len(os.listdir(CHROMA_DIR)) > 0

    # 1. Handle Caching vs New Uploads
    if has_cache:
        use_cache = input("\n[?] Found an existing database. Load it? (Y/n): ").strip().lower()
        if use_cache in ['', 'y', 'yes']:
            vectorstore, bm25_retriever = load_and_index_directory()
        else:
            reset = input("[?] Overwrite old database with new files? (Y/n): ").strip().lower()
            if reset in ['', 'y', 'yes']:
                shutil.rmtree(CHROMA_DIR)
                os.makedirs(CHROMA_DIR, exist_ok=True)

            user_pdf_paths = get_user_pdfs()
            if not user_pdf_paths:
                print("No files to process. Exiting system.")
                exit(0)
            vectorstore, bm25_retriever = load_and_index_directory(user_pdf_paths)
    else:
        user_pdf_paths = get_user_pdfs()
        if not user_pdf_paths:
            print("No files to process. Exiting system.")
            exit(0)
        vectorstore, bm25_retriever = load_and_index_directory(user_pdf_paths)

    # 2. Start Chat Interface
    if vectorstore is None:
        print("Initialization failed.")
    else:
        print("\n Open-Domain Advanced Auditing System Active! Type 'exit' to quit.")
        chat_history = []
        while True:
            user_input = input("\nYou: ")
            if user_input.lower() in ['exit', 'quit']:
                break
            if not user_input.strip():
                continue

            inputs = {
                "query": user_input,
                "rewritten_queries": [],
                "entity_scope": "General",
                "chat_history": chat_history,
                "retrieved_docs": []
            }
            config = {"configurable": {"thread_id": "max_accuracy_user"}}

            for output in app.stream(inputs, config=config):
                for key, value in output.items():
                    if key == "generate":
                        print(f"\n Bot Response:\n{value['response']}")
                        chat_history.extend(value["chat_history"])
