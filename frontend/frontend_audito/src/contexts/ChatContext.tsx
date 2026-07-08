import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useAuth } from "@/contexts/AuthContext";
import { chatService } from "@/services/chat";
import { uploadService } from "@/services/upload";
import { toast } from "sonner";
import type {
  Conversation,
  Message,
  ParsingStage,
  ParsingStageKey,
  UploadedDoc,
} from "@/types";

interface ChatContextValue {
  conversations: Conversation[];
  activeId: string | null;
  activeConversation: Conversation | null;
  isStreaming: boolean;
  documents: UploadedDoc[];

  createConversation: () => string;
  selectConversation: (id: string) => void;
  renameConversation: (id: string, title: string) => void;
  deleteConversation: (id: string) => void;
  sendMessage: (content: string) => Promise<void>;
  stopGeneration: () => void;
  regenerate: (messageId: string) => Promise<void>;
  reactMessage: (messageId: string, liked: boolean | null) => void;

  uploadDocument: (file: File) => Promise<void>;
  removeDocument: (id: string) => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

// IMPORTANT: every storage key is namespaced by the logged-in user's id.
// The previous version used one global "audito_conversations" /
// "audito_docs" key for everyone, in shared localStorage — so two people
// logged in on two tabs of the same browser stomped on each other's data.
// sessionStorage additionally scopes this to one tab/browsing context.
const convKey = (userId: string) => `audito_conversations_${userId}`;
const docKey = (userId: string) => `audito_docs_${userId}`;

const STAGE_DEFS: { key: ParsingStageKey; label: string }[] = [
  { key: "upload", label: "Upload Complete" },
  { key: "extract", label: "Extracting Text" },
  { key: "ocr", label: "OCR Processing" },
  { key: "tables", label: "Detecting Tables" },
  { key: "chunks", label: "Creating Chunks" },
  { key: "embeddings", label: "Creating Embeddings" },
  { key: "vectordb", label: "Saving to Vector Database" },
  { key: "ready", label: "Ready" },
];

function buildStages(): ParsingStage[] {
  return STAGE_DEFS.map((s) => ({ ...s, status: "waiting" as const }));
}

// Marks every stage before `targetIndex` as done, and `targetIndex` itself as
// processing. Used to translate the backend's coarse Celery states
// (PARSING / EMBEDDING / SUCCESS) into the UI's more granular stage list —
// the backend doesn't currently report per-page OCR/table progress, so
// stages within one Celery state advance together rather than individually.
function stagesUpTo(targetIndex: number): ParsingStage[] {
  return STAGE_DEFS.map((s, i) => ({
    ...s,
    status: i < targetIndex ? "done" : i === targetIndex ? "processing" : "waiting",
  }));
}

function summarizeTitle(text: string) {
  const clean = text.trim().replace(/\s+/g, " ");
  if (clean.length <= 40) return clean;
  return clean.slice(0, 40).trimEnd() + "…";
}

function backendHistoryToMessages(history: { sender: "user" | "bot"; text: string }[]): Message[] {
  return history.map((h, i) => ({
    id: `${i}-${h.sender}-${h.text.slice(0, 12)}`,
    role: h.sender === "user" ? "user" : "assistant",
    content: h.text,
    createdAt: Date.now(),
  }));
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<UploadedDoc[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const stopRef = useRef(false);

  // Re-hydrate whenever the logged-in user changes (login, logout, or
  // switching accounts in the same tab).
  useEffect(() => {
    if (!user) {
      setConversations([]);
      setDocuments([]);
      setActiveId(null);
      return;
    }

    // 1. Instant paint from this tab's local cache, if any.
    try {
      const rawC = window.sessionStorage.getItem(convKey(user.id));
      setConversations(rawC ? JSON.parse(rawC) : []);
      const rawD = window.sessionStorage.getItem(docKey(user.id));
      setDocuments(rawD ? JSON.parse(rawD) : []);
    } catch {
      setConversations([]);
      setDocuments([]);
    }
    setActiveId(null);

    // 2. Authoritative refresh from Postgres. This is what makes a chat
    // from yesterday still show up today — sessionStorage is wiped when
    // the browser closes, but the backend now persists every conversation
    // in the `conversations` table regardless of what's left in this tab.
    chatService
      .listConversations()
      .then((res) => {
        const backendConvs = res.data.conversations ?? [];
        setConversations((prev) => {
          const byId = new Map(prev.map((c) => [c.id, c]));
          return backendConvs.map((bc) => {
            const existing = byId.get(bc.session_id);
            return {
              id: bc.session_id,
              title: bc.title || existing?.title || "New Chat",
              messages: existing?.messages ?? [],
              updatedAt: bc.updated_at ? new Date(bc.updated_at).getTime() : (existing?.updatedAt ?? Date.now()),
              documentIds: existing?.documentIds ?? [],
            };
          });
        });
      })
      .catch(() => {
        // backend unreachable — keep whatever local cache we already loaded
      });
  }, [user?.id]);

  useEffect(() => {
    if (!user) return;
    window.sessionStorage.setItem(convKey(user.id), JSON.stringify(conversations));
  }, [conversations, user?.id]);

  useEffect(() => {
    if (!user) return;
    window.sessionStorage.setItem(docKey(user.id), JSON.stringify(documents));
  }, [documents, user?.id]);

  const activeConversation = useMemo(
    () => conversations.find((c) => c.id === activeId) ?? null,
    [conversations, activeId],
  );

  const createConversation = useCallback(() => {
    // The conversation id IS the backend session_id — the backend has no
    // separate "conversation" resource, a session_id string scopes both
    // documents and chat history for one thread.
    const id = crypto.randomUUID();
    const conv: Conversation = {
      id,
      title: "New Chat",
      messages: [],
      updatedAt: Date.now(),
      documentIds: [],
    };
    setConversations((prev) => [conv, ...prev]);
    setActiveId(id);
    return id;
  }, []);

  // Selecting a conversation pulls its authoritative message history from
  // the backend (in case this thread was used from another device/tab),
  // instead of trusting only what's cached locally.
  const selectConversation = useCallback(
    (id: string) => {
      setActiveId(id);
      if (!user) return;
      chatService
        .history(String(user.id), id)
        .then((res) => {
          const backendMessages = backendHistoryToMessages(res.data.history ?? []);
          setConversations((prev) =>
            prev.map((c) => (c.id === id ? { ...c, messages: backendMessages } : c)),
          );
        })
        .catch(() => {
          // No history yet for this session, or backend unreachable — keep local cache.
        });

      chatService
        .listFiles(id)
        .then((res) => {
          const files = res.data.files ?? [];
          setDocuments((prev) => {
            const existingIds = new Set(prev.map((d) => d.id));
            const rebuilt: UploadedDoc[] = files
              .filter((f) => !existingIds.has(f.id))
              .map((f) => ({
                id: f.id,
                name: f.name,
                size: 0,
                uploadedAt: f.created_at ? new Date(f.created_at).getTime() : Date.now(),
                status: f.status === "indexed" ? "indexed" : f.status === "failed" ? "failed" : "processing",
                progress: f.status === "indexed" ? 100 : 0,
                stages: buildStages().map((s) => ({
                  ...s,
                  status: f.status === "indexed" ? ("done" as const) : s.status,
                })),
              }));
            return [...prev, ...rebuilt];
          });
          setConversations((prev) =>
            prev.map((c) =>
              c.id === id
                ? { ...c, documentIds: Array.from(new Set([...c.documentIds, ...files.map((f) => f.id)])) }
                : c,
            ),
          );
        })
        .catch(() => {
          // No files for this session, or backend unreachable — keep local cache.
        });
    },
    [user],
  );

  const renameConversation = useCallback((id: string, title: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title: title.trim() || c.title } : c)),
    );
  }, []);

  const deleteConversation = useCallback(
    (id: string) => {
      // Also wipes this session's vectors + history on the backend, so
      // deleting a conversation in the UI doesn't leave orphaned data indexed
      // under that session_id forever.
      chatService.clearSession(id).catch(() => {
        // best-effort — still remove locally even if the backend call fails
      });
      const docIds = conversations.find((c) => c.id === id)?.documentIds ?? [];
      setConversations((prev) => prev.filter((c) => c.id !== id));
      setDocuments((prev) => prev.filter((d) => !docIds.includes(d.id)));
      setActiveId((cur) => (cur === id ? null : cur));
    },
    [conversations],
  );

  const updateConversation = useCallback(
    (id: string, updater: (c: Conversation) => Conversation) => {
      setConversations((prev) => prev.map((c) => (c.id === id ? updater(c) : c)));
    },
    [],
  );

  // Reveals real backend text progressively client-side for a "typing" feel,
  // instead of the old version which streamed pre-canned SAMPLE_REPLIES.
  const revealAssistantText = useCallback(
    async (conversationId: string, assistantId: string, fullText: string) => {
      stopRef.current = false;
      setIsStreaming(true);
      const tokens = fullText.split(/(\s+)/);
      let acc = "";
      for (const tok of tokens) {
        if (stopRef.current) {
          acc = fullText; // stopping just snaps to the full real answer
          break;
        }
        acc += tok;
        await new Promise((r) => setTimeout(r, 12));
        updateConversation(conversationId, (c) => ({
          ...c,
          updatedAt: Date.now(),
          messages: c.messages.map((m) => (m.id === assistantId ? { ...m, content: acc } : m)),
        }));
      }
      updateConversation(conversationId, (c) => ({
        ...c,
        messages: c.messages.map((m) => (m.id === assistantId ? { ...m, content: fullText } : m)),
      }));
      setIsStreaming(false);
    },
    [updateConversation],
  );

  const sendMessage = useCallback(
    async (content: string) => {
      const text = content.trim();
      if (!text || !user) return;

      let convId = activeId;
      if (!convId) convId = createConversation();

      const userMsg: Message = {
        id: crypto.randomUUID(),
        role: "user",
        content: text,
        createdAt: Date.now(),
      };

      updateConversation(convId, (c) => ({
        ...c,
        title: c.messages.length === 0 ? summarizeTitle(text) : c.title,
        messages: [...c.messages, userMsg],
        updatedAt: Date.now(),
      }));

      const assistantId = crypto.randomUUID();
      updateConversation(convId, (c) => ({
        ...c,
        messages: [...c.messages, { id: assistantId, role: "assistant", content: "", createdAt: Date.now() }],
      }));

      try {
        const res = await chatService.send(text, convId);
        await revealAssistantText(convId, assistantId, res.data.response);
      } catch (err: any) {
        const detail = err?.response?.data?.detail ?? "Something went wrong reaching the backend.";
        updateConversation(convId, (c) => ({
          ...c,
          messages: c.messages.map((m) => (m.id === assistantId ? { ...m, content: `⚠️ ${detail}` } : m)),
        }));
        toast.error(detail);
      }
    },
    [activeId, createConversation, revealAssistantText, updateConversation, user],
  );

  const stopGeneration = useCallback(() => {
    stopRef.current = true;
  }, []);

  const regenerate = useCallback(
    async (messageId: string) => {
      if (!activeId || !user) return;
      const conv = conversations.find((c) => c.id === activeId);
      if (!conv) return;
      const idx = conv.messages.findIndex((m) => m.id === messageId);
      if (idx < 1) return;
      const prompt = conv.messages[idx - 1]?.content ?? "";

      updateConversation(activeId, (c) => ({ ...c, messages: c.messages.slice(0, idx) }));

      const assistantId = crypto.randomUUID();
      updateConversation(activeId, (c) => ({
        ...c,
        messages: [...c.messages, { id: assistantId, role: "assistant", content: "", createdAt: Date.now() }],
      }));

      try {
        const res = await chatService.send(prompt, activeId);
        await revealAssistantText(activeId, assistantId, res.data.response);
      } catch (err: any) {
        toast.error(err?.response?.data?.detail ?? "Regeneration failed.");
      }
    },
    [activeId, conversations, revealAssistantText, updateConversation, user],
  );

  const reactMessage = useCallback(
    (messageId: string, liked: boolean | null) => {
      if (!activeId) return;
      updateConversation(activeId, (c) => ({
        ...c,
        messages: c.messages.map((m) => (m.id === messageId ? { ...m, liked } : m)),
      }));
    },
    [activeId, updateConversation],
  );

  // ---- Real document upload: enqueue on the backend (Celery), then poll ----
  const pollUploadStatus = useCallback(
    (docId: string, taskId: string, conversationId: string) => {
      let cancelled = false;

      const interval = setInterval(async () => {
        if (cancelled) return;
        try {
          const res = await uploadService.status(taskId);
          const state = res.data.state;

          if (state === "PARSING") {
            // Advance one stage at a time through extract/ocr/tables/chunks
            // while the worker is in this phase — the backend only reports
            // "PARSING" as one coarse state, not per-page granularity, so we
            // approximate progress visually rather than lying about 100%.
            setDocuments((prev) =>
              prev.map((d) => {
                if (d.id !== docId) return d;
                const currentIdx = d.stages.findIndex((s) => s.status === "processing");
                const nextIdx = Math.min(currentIdx < 0 ? 1 : currentIdx + 1, 4);
                return { ...d, status: "processing", stages: stagesUpTo(nextIdx) };
              }),
            );
          } else if (state === "EMBEDDING") {
            setDocuments((prev) =>
              prev.map((d) => (d.id === docId ? { ...d, status: "processing", stages: stagesUpTo(5) } : d)),
            );
          } else if (state === "SUCCESS") {
            clearInterval(interval);
            if (res.data.status === "failed") {
              setDocuments((prev) => prev.map((d) => (d.id === docId ? { ...d, status: "failed" } : d)));
              toast.error(res.data.detail ?? "Processing failed.");
              return;
            }
            setDocuments((prev) =>
              prev.map((d) =>
                d.id === docId
                  ? { ...d, status: "indexed", progress: 100, stages: buildStages().map((s) => ({ ...s, status: "done" as const })) }
                  : d,
              ),
            );
            updateConversation(conversationId, (c) => ({
              ...c,
              documentIds: c.documentIds.includes(docId) ? c.documentIds : [...c.documentIds, docId],
            }));
            toast.success(`${res.data.file_name ?? "Document"} indexed (${res.data.total_chunks_indexed ?? "?"} chunks).`);
          } else if (state === "FAILURE") {
            clearInterval(interval);
            setDocuments((prev) => prev.map((d) => (d.id === docId ? { ...d, status: "failed" } : d)));
            toast.error(res.data.detail ?? "Upload processing failed.");
          }
          // PENDING: worker hasn't picked it up yet — keep waiting, no change yet
        } catch {
          // transient network hiccup while polling — try again next tick
        }
      }, 1200);

      return () => {
        cancelled = true;
        clearInterval(interval);
      };
    },
    [updateConversation],
  );

  const uploadDocument = useCallback(
    async (file: File) => {
      if (!user) {
        toast.error("You must be logged in to upload documents.");
        return;
      }

      let convId = activeId;
      if (!convId) convId = createConversation();

      const docId = crypto.randomUUID();
      const doc: UploadedDoc = {
        id: docId,
        name: file.name,
        size: file.size,
        uploadedAt: Date.now(),
        status: "queued",
        progress: 0,
        stages: buildStages(),
      };
      setDocuments((prev) => [doc, ...prev]);

      try {
        const res = await uploadService.upload(file, convId, (pct) => {
          setDocuments((prev) =>
            prev.map((d) => (d.id === docId ? { ...d, progress: pct } : d)),
          );
        });

        setDocuments((prev) =>
          prev.map((d) => (d.id === docId ? { ...d, progress: 100, status: "processing", stages: stagesUpTo(1) } : d)),
        );

        pollUploadStatus(docId, res.data.task_id, convId);
      } catch (err: any) {
        setDocuments((prev) => prev.map((d) => (d.id === docId ? { ...d, status: "failed" } : d)));
        toast.error(err?.response?.data?.detail ?? "Upload failed.");
      }
    },
    [activeId, createConversation, pollUploadStatus, user],
  );

  const removeDocument = useCallback((id: string) => {
    // Local-only: the backend has no per-document delete endpoint, only a
    // whole-session clear (see deleteConversation). This just hides it from
    // the sidebar; the underlying vectors remain indexed under the session
    // until the whole conversation/session is deleted.
    setDocuments((prev) => prev.filter((d) => d.id !== id));
  }, []);

  const value: ChatContextValue = {
    conversations,
    activeId,
    activeConversation,
    isStreaming,
    documents,
    createConversation,
    selectConversation,
    renameConversation,
    deleteConversation,
    sendMessage,
    stopGeneration,
    regenerate,
    reactMessage,
    uploadDocument,
    removeDocument,
  };

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) throw new Error("useChat must be used inside ChatProvider");
  return ctx;
}
