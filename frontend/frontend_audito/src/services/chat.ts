import { api } from "./api";

// Matches backend/app/main.py exactly.
// The backend has no concept of separate "conversations" as a REST resource —
// a single `session_id` string scopes both the uploaded documents AND the
// chat history for one thread. The frontend treats one local Conversation.id
// as that session_id.

export interface BackendHistoryEntry {
  sender: "user" | "bot";
  text: string;
}

export interface Citation {
  source: string;
  page: number | null;
  file_id: string | null;
}

export interface ChatResponse {
  status: string;
  response: string;
  sub_queries_used: string[];
  follow_up_questions: string[];
  citations: Citation[];
}

export interface BackendConversation {
  session_id: string;
  title: string;
  updated_at: string | null;
  created_at: string | null;
}

export interface BackendFile {
  id: string;
  name: string;
  status: string;
  total_chunks_indexed: number;
  created_at: string | null;
}

export const chatService = {
  // Rebuilds the sidebar from Postgres — this is what makes chat history
  // still show up after closing the browser overnight, instead of only
  // existing for as long as this tab's sessionStorage survives.
  listConversations: () =>
    api.get<{ conversations: BackendConversation[] }>("/api/conversations"),

  listFiles: (sessionId: string) =>
    api.get<{ files: BackendFile[] }>(`/api/conversations/${sessionId}/files`),

  history: (userId: string, sessionId: string) =>
    api.get<{ history: BackendHistoryEntry[] }>(
      `/api/chat/history/${userId}/${sessionId}`,
    ),

  // fileIds is optional — pass the ids of the documents checked in the
  // sidebar to restrict the answer to just those files (e.g. Sam picking
  // 2 of her 5 uploaded PDFs). Omit or pass an empty array to search across
  // everything she's uploaded, same as before.
  send: (query: string, sessionId: string, fileIds?: string[]) => {
    const form = new FormData();
    form.append("query", query);
    form.append("session_id", sessionId);
    if (fileIds && fileIds.length > 0) {
      form.append("file_ids", fileIds.join(","));
    }
    return api.post<ChatResponse>("/api/chat", form);
  },

  clearSession: (sessionId: string) =>
    api.delete(`/api/session/${sessionId}`),

  // Deletes ONE uploaded document (e.g. one of two Tata PDFs) without
  // wiping the whole chat — distinct from clearSession above.
  deleteDocument: (fileId: string) =>
    api.delete(`/api/documents/${fileId}`),
};
