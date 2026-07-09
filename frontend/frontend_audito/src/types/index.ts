export type Role = "user" | "assistant";

export interface Message {
  id: string;
  role: Role;
  content: string;
  createdAt: number;
  liked?: boolean | null;
  followUps?: string[];
  citations?: { source: string; page: number | null; file_id: string | null }[];
}

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  updatedAt: number;
  documentIds: string[];
}

export type ParsingStageKey =
  | "upload"
  | "extract"
  | "ocr"
  | "tables"
  | "chunks"
  | "embeddings"
  | "vectordb"
  | "ready";

export type StageStatus = "waiting" | "processing" | "done" | "failed";

export interface ParsingStage {
  key: ParsingStageKey;
  label: string;
  status: StageStatus;
}

export type DocStatus = "queued" | "processing" | "indexed" | "failed";

export interface UploadedDoc {
  id: string;
  name: string;
  size: number;
  uploadedAt: number;
  status: DocStatus;
  progress: number;
  stages: ParsingStage[];
  // Whether this doc is checked in the sidebar to scope the next question.
  // true (default) = included; unchecked docs are excluded from retrieval.
  selected: boolean;
}

export interface User {
  id: string;
  name: string;
  email: string;
}
