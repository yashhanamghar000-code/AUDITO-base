export type Role = "user" | "assistant";

export interface Message {
  id: string;
  role: Role;
  content: string;
  createdAt: number;
  liked?: boolean | null;
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
}

export interface User {
  id: string;
  name: string;
  email: string;
}
