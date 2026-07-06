import re
import json
from typing import List, Dict, Any, TypedDict, Annotated
from operator import add

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langchain_core.documents import Document

# Import multi-user retriever class and configs
from app.config import llm, TOP_K_PER_QUERY, FINAL_DOCS_PER_QUERY, MAX_TOTAL_CONTEXT_DOCS
from app.retriever import MultiUserRetriever


class AgentState(TypedDict):
    query: str
    user_id: str          # Tracks multi-tenant execution context
    session_id: str       # Tracks specific chat thread context
    chat_history: Annotated[list, add]
    sub_queries: List[str]
    retrieved_docs: List[Document]
    response: str


def decompose_query(query: str, chat_history: List[str]) -> List[str]:
    history_str = "\n".join(chat_history[-6:]) if chat_history else "No prior history."
    decomposition_prompt = (
        "You are an advanced Query Rewriter and Decomposition engine optimized for dense financial data retrieval.\n"
        "Your task is to produce 1 to 3 optimized search queries for analyzing financial annual reports based on the user question.\n\n"
        "OUTPUT REQUIREMENT:\nReturn ONLY a JSON list of strings, nothing else. Do not wrap it in markdown fences.\n\n"
        f"Chat History:\n{history_str}\n\nRaw User Input: {query}"
    )
    try:
        resp = llm.invoke([HumanMessage(content=decomposition_prompt)])
        raw = resp.content.strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
        queries = json.loads(raw)
        if isinstance(queries, list) and len(queries) > 0:
            return queries[:3]
    except Exception as e:
        print(f" Query optimization failed ({e}), falling back to raw query.")
    return [query]


def decompose_node(state: AgentState):
    print(f"\n[Workflow] Stage 0: Decomposing query for User: {state['user_id']}...")
    sub_queries = decompose_query(state["query"], state["chat_history"])
    print(f"   -> Sub-Queries Generated: {sub_queries}")
    return {"sub_queries": sub_queries}


def build_workflow():
    """
    Constructs the multi-user RAG compilation graph.
    No longer requires static database instances passed as arguments.
    """

    def retrieve_node(state: AgentState):
        print(f"[Workflow] Stage 1: Executing multi-doc hybrid search loop...")
        all_final_docs = []
        seen = set()

        for sub_q in state["sub_queries"]:
            # Uses our secure, isolated retriever method per sub-query
            candidate_docs = MultiUserRetriever.hybrid_search(
                query=sub_q,
                user_id=state["user_id"],
                session_id=state["session_id"],
                top_k=TOP_K_PER_QUERY
            )

            if not candidate_docs:
                continue

            print(f"[Workflow] Stage 2: Cross-Encoder filtering for: '{sub_q}'")
            # Passes candidate docs through our class-based cross-encoder reranker
            from app.retriever import reranker
            pairs = [[sub_q, doc.page_content] for doc in candidate_docs]
            rerank_scores = reranker.predict(pairs)
            scored_docs = sorted(zip(rerank_scores, candidate_docs), key=lambda x: x[0], reverse=True)
            verified = [doc for score, doc in scored_docs[:FINAL_DOCS_PER_QUERY]]

            for doc in verified:
                key = (doc.metadata.get("source"), doc.metadata.get("page"), doc.page_content[:80])
                if key not in seen:
                    seen.add(key)
                    all_final_docs.append(doc)

        all_final_docs = all_final_docs[:MAX_TOTAL_CONTEXT_DOCS]
        if all_final_docs:
            print(f"   -> Context Pipeline Ready. Top Segment Match: {all_final_docs[0].metadata.get('source')}, Page {all_final_docs[0].metadata.get('page')}\n")
        return {"retrieved_docs": all_final_docs}

    def generate_node(state: AgentState):
        if not state["retrieved_docs"]:
            return {"response": "Data could not be localized within any current report segments.", "chat_history": []}

        context_blocks = []
        for i, d in enumerate(state["retrieved_docs"], start=1):
            context_blocks.append(f"[CHUNK {i} | Source: {d.metadata.get('source')} | Page: {d.metadata.get('page')}]\n{d.page_content}")
        context_str = "\n\n".join(context_blocks)

        system_prompt = (
            "You are an expert precision financial and legal auditor. Your task is to answer the user's question with absolute data integrity.\n"
            "Use ONLY the facts and structural context provided.\n\n"
            "CRITICAL RULE : If the data can be printed in the table format then print it, but do not try to print every answer in a table format."
            "CRITICAL RULE 1: If a metric or number in the text is accompanied by modifiers like 'over', 'more than', 'approx', or 'nearly', "
            "you MUST include that modifier in your final answer.\n"
            "CRITICAL RULE 2: If the context contains a Markdown table or data blocks relevant to the user's question, you MUST present the data "
            "using a clean, standard Markdown table layout. \n"
            "CRITICAL: Every row of your markdown table MUST be on its own line, separated by an actual physical newline carriage return (\\n). "
            "NEVER combine rows side-by-side using spaces, words, or double pipes '||'. Each row must start with '|' and end with '|\\n'.\n"
            "Leave exactly one blank newline before and after the table entirely.\n"
            "CRITICAL RULE 3: ANTI-HALLUCINATION GUARD. Do not answer anything not provided in the text."
        )

        user_prompt = f"Context:\n{context_str}\n\nQuestion: {state['query']}"
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])

        return {
            "response": response.content,
            "chat_history": [f"User: {state['query']}", f"Bot: {response.content}"]
        }

    # Build the graph topology
    workflow = StateGraph(AgentState)
    workflow.add_node("decompose", decompose_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("generate", generate_node)

    workflow.add_edge(START, "decompose")
    workflow.add_edge("decompose", "retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", END)

    return workflow.compile()
