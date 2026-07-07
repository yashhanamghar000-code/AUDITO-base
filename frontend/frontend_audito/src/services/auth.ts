import { api } from "./api";
import type { User } from "@/types";

interface TokenResponse {
  token: string;
  user: User;
}

// Matches backend/app/routes_auth.py exactly: prefix is /api/auth
export const authService = {
  login: (email: string, password: string) =>
    api.post<TokenResponse>("/api/auth/login", { email, password }),
  register: (name: string, email: string, password: string) =>
    api.post<TokenResponse>("/api/auth/register", { name, email, password }),
  me: () => api.get<User>("/api/auth/me"),
  logout: () => api.post("/api/auth/logout"),
};
