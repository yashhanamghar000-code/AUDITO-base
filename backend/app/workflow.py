import re
import json
from typing import List, TypedDict, Annotated
from operator import add

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langchain_core.documents import Document

# Import multi-user retriever class and configs
from app.config import llm, TOP_K_PER_QUERY, FINAL_DOCS_PER_QUERY, MAX_TOTAL_CONTEXT_DOCS
from app.retriever import MultiUserRetriever

FOLLOWUP_DELIMITER = "###FOLLOWUPS###"


class AgentState(TypedDict):
    query: str
    user_id: str          # Tracks multi-tenant execution context
    session_id: str       # Tracks specific chat thread context
    chat_history: Annotated[list, add]
    sub_queries: List[str]
    retrieved_docs: List[Document]
    response: str
    follow_up_questions: List[str]
    citations: List[dict]
    selected_file_ids: List[str]


def decompose_query(query: str, chat_history: List[str]) -> List[str]:
    history_str = "\n".join(chat_history[-6:]) if chat_history else "No prior history."
    decomposition_prompt = (
        "You are an advanced Query Rewriter and Decomposition engine optimized for dense financial data retrieval.\n"
        "Your task is to produce 1 to 3 optimized search queries for analyzing financial annual reports based on the user question.\n\n"
        "CRITICAL: If the question compares, contrasts, or asks about MULTIPLE named entities/companies "
        "(e.g. 'compare Tata and Mahindra', 'net profit for both companies'), you MUST generate one separate "
        "sub-query PER entity, each explicitly naming that entity (e.g. 'Tata Motors net profit', "
        "'Mahindra net profit') — never a single merged query that risks one entity's data being outranked "
        "and dropped entirely from the retrieved context.\n\n"
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


def _split_answer_and_followups(raw_content: str):
    """
    Splits one LLM response into (answer_text, follow_up_questions).
    The model is asked to emit the delimiter + a JSON list at the very end
    of its response, in the SAME call that produced the answer — this
    avoids a second llm.invoke() round-trip on every single chat message
    just to get 3 follow-up suggestions. Falls back to (raw_content, [])
    on any parsing failure, so a malformed delimiter/JSON never breaks the
    actual answer the user is waiting for.
    """
    if FOLLOWUP_DELIMITER not in raw_content:
        return raw_content.strip(), []

    answer_part, _, followup_part = raw_content.partition(FOLLOWUP_DELIMITER)
    answer_part = answer_part.strip()

    followup_part = followup_part.strip()
    followup_part = re.sub(r"^```(json)?|```$", "", followup_part, flags=re.MULTILINE).strip()

    try:
        questions = json.loads(followup_part)
        if isinstance(questions, list):
            return answer_part, [str(q).strip() for q in questions if str(q).strip()][:3]
    except Exception as e:
        print(f" Follow-up question parsing failed ({e}), returning answer without follow-ups.")

    return answer_part, []


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
        # Grouped by source document, not a flat list — this is what lets us
        # guarantee every document represented in this session gets a fair
        # shot at the final context, instead of whichever sub-query happened
        # to run first (or scored marginally higher) taking every slot.
        docs_by_source: dict = {}
        seen = set()

        # Sidebar checkbox selection — when the user has checked specific
        # documents, every sub-query's retrieval is restricted to just
        # those files. Empty/absent = search across all of this user's
        # documents (the default, unrestricted behavior).
        selected_file_ids = state.get("selected_file_ids") or None

        for sub_q in state["sub_queries"]:
            # Uses our secure, isolated retriever method per sub-query
            candidate_docs = MultiUserRetriever.hybrid_search(
                query=sub_q,
                user_id=state["user_id"],
                session_id=state["session_id"],
                top_k=TOP_K_PER_QUERY,
                file_ids=selected_file_ids,
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
                    src = doc.metadata.get("source", "unknown")
                    docs_by_source.setdefault(src, []).append(doc)

        # Round-robin across sources (one doc from each source per round,
        # each source's docs kept in their own relevance order) instead of
        # a flat concatenation — this is the actual fix for "asking about
        # Tata sometimes returns nothing" when Mahindra is also in this
        # session: a flat list biased toward whichever document had more/
        # higher-scoring chunks would get truncated to all-Mahindra before
        # a comparative query's Tata chunks ever got a chance.
        all_final_docs = []
        sources = list(docs_by_source.keys())
        idx = 0
        while any(docs_by_source.values()):
            src = sources[idx % len(sources)]
            if docs_by_source[src]:
                all_final_docs.append(docs_by_source[src].pop(0))
            idx += 1
            if idx > 10000:  # safety valve, should never trigger
                break

        all_final_docs = all_final_docs[:MAX_TOTAL_CONTEXT_DOCS]
        if all_final_docs:
            print(f"   -> Context Pipeline Ready. Top Segment Match: {all_final_docs[0].metadata.get('source')}, Page {all_final_docs[0].metadata.get('page')}\n")
        return {"retrieved_docs": all_final_docs}

    def generate_node(state: AgentState):
        if not state["retrieved_docs"]:
            return {
                "response": "Data could not be localized within any current report segments.",
                "chat_history": [],
                "follow_up_questions": [],
                "citations": [],
            }

        context_blocks = []
        for i, d in enumerate(state["retrieved_docs"], start=1):
            context_blocks.append(f"[CHUNK {i} | Source: {d.metadata.get('source')} | Page: {d.metadata.get('page')}]\n{d.page_content}")
        context_str = "\n\n".join(context_blocks)

        # Top 3 unique (source, page) pairs, in the order retrieved_docs is
        # already sorted (interleaved-then-reranked relevance order) — this
        # is what the frontend needs to show "Sources: FileX p.12, p.45..."
        citations = []
        seen_citation_keys = set()
        for d in state["retrieved_docs"]:
            key = (d.metadata.get("source"), d.metadata.get("page"))
            if key not in seen_citation_keys:
                seen_citation_keys.add(key)
                citations.append({"source": d.metadata.get("source"), "page": d.metadata.get("page")})
            if len(citations) >= 3:
                break

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
            "CRITICAL RULE 4: SOURCE TABLE INTEGRITY. Tables extracted from the source PDF may have merged or multi-row headers that got "
            "flattened, or a header row whose column count doesn't match the data rows below it. Do NOT reproduce a malformed table "
            "structure verbatim. Instead: identify each data value's actual meaning from the surrounding labels and context, and rebuild a "
            "clean table with one label per row/column. If you cannot confidently determine which header a given number belongs to, state "
            "the number as prose with its label instead of forcing it into an uncertain table cell — never guess a header-to-value pairing.\n"
            "NEVER copy a chunk's raw '| ... | ... |' markdown syntax directly into your answer unedited — always reformat into a clean "
            "table of your own construction, using only the labels and values you can confidently pair.\n"
            "CRITICAL RULE 3: ANTI-HALLUCINATION GUARD. Do not answer anything not provided in the text.\n\n"
            "CRITICAL RULE 6: NO INTERNAL LEAKAGE. The words 'CHUNK', 'chunk', 'context', 'the provided context', 'the given information', "
            "'the provided text', and any [CHUNK N | Source: ... | Page: ...] labels are internal retrieval scaffolding for YOUR reference "
            "only — the user must NEVER see them. Never write phrases like 'According to CHUNK 3' or 'based on the provided context' or "
            "'reviewing the provided chunks'. Instead write as if you personally read the full report — e.g. 'The report states...', "
            "'According to the FY24 annual report...', or just state the fact directly with no meta-reference to how you found it.\n"
            "CRITICAL RULE 7: DIRECT ANSWER STYLE. Do not narrate your search process (e.g. 'To answer this, we need to look at...', "
            "'Let's check if this matches...', 'Upon reviewing...'). Give the final answer directly and confidently. If the answer isn't "
            "available, say so in one concise sentence (e.g. 'The report doesn't specify this figure.') — do not walk through a multi-step "
            "process of what you tried and failed to find before concluding that.\n\n"
            "CRITICAL RULE 5: FOLLOW-UP QUESTIONS. After your complete answer, on its own new line, output exactly the delimiter "
            f"{FOLLOWUP_DELIMITER} followed immediately by a JSON list of exactly 3 short follow-up questions (each under 12 words) "
            "that the user is likely to ask next, answerable from this same context. Do not repeat the original question. "
            "Output nothing else after the JSON list — no markdown fences, no commentary. If you cannot think of 3 good follow-ups "
            f"grounded in this context, output {FOLLOWUP_DELIMITER} followed by an empty JSON list []."
        )

        user_prompt = f"Context:\n{context_str}\n\nQuestion: {state['query']}"
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])

        # One LLM call produces both the answer and the follow-up
        # suggestions — a second llm.invoke() just for follow-ups would add
        # a full extra network round-trip to every chat message.
        answer_text, follow_up_questions = _split_answer_and_followups(response.content)

        return {
            "response": answer_text,
            "chat_history": [f"User: {state['query']}", f"Bot: {answer_text}"],
            "follow_up_questions": follow_up_questions,
            "citations": citations,
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
