import axios from "axios";

// Your FastAPI backend. Override with VITE_API_BASE_URL in .env for deployment.
export const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000";

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000, // parsing/LLM calls can be slow; upload itself returns fast via Celery
});

// IMPORTANT: sessionStorage, not localStorage.
// localStorage is shared across every tab of the same browser origin, so two
// tabs logged in as two different users would silently overwrite each
// other's token. sessionStorage is scoped to a single tab/browsing context,
// so opening the app in two tabs and logging in as two different users
// keeps each tab's session fully independent.
api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = window.sessionStorage.getItem("audito_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});
