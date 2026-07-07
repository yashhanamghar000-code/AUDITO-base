import { api } from "./api";

// Matches backend/app/main.py exactly. Upload enqueues a Celery task and
// returns immediately with a task_id; the frontend polls /api/upload/status
// to drive the parsing-stage UI, instead of blocking on one giant request.

export interface UploadQueuedResponse {
  status: "queued";
  task_id: string;
}

export type UploadTaskState =
  | "PENDING"
  | "PARSING"
  | "EMBEDDING"
  | "SUCCESS"
  | "FAILURE";

export interface UploadStatusResponse {
  state: UploadTaskState;
  detail?: string;
  status?: "success" | "failed";
  total_chunks_indexed?: number;
  file_name?: string;
}

export const uploadService = {
  upload: (file: File, sessionId: string, onProgress?: (pct: number) => void) => {
    const form = new FormData();
    form.append("session_id", sessionId);
    form.append("file", file);
    return api.post<UploadQueuedResponse>("/api/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (evt) => {
        if (onProgress && evt.total) {
          onProgress(Math.round((evt.loaded / evt.total) * 100));
        }
      },
    });
  },

  status: (taskId: string) =>
    api.get<UploadStatusResponse>(`/api/upload/status/${taskId}`),
};
