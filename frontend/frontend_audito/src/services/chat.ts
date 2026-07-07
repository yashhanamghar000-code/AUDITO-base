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

export interface ChatResponse {
  status: string;
  response: string;
  sub_queries_used: string[];
}

export const chatService = {
  history: (userId: string, sessionId: string) =>
    api.get<{ history: BackendHistoryEntry[] }>(
      `/api/chat/history/${userId}/${sessionId}`,
    ),

  send: (query: string, sessionId: string) => {
    const form = new FormData();
    form.append("query", query);
    form.append("session_id", sessionId);
    return api.post<ChatResponse>("/api/chat", form);
  },

  clearSession: (sessionId: string) =>
    api.delete(`/api/session/${sessionId}`),
};
