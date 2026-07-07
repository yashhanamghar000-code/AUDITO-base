import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { authService } from "@/services/auth";
import type { User } from "@/types";

interface AuthContextValue {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
  updateProfile: (patch: Partial<User>) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// IMPORTANT: sessionStorage, not localStorage.
// localStorage is shared across every tab of the same browser origin, so two
// tabs logged in as two different users would silently overwrite each
// other's token/profile — which is exactly the "yash's data shows up under
// atharva" bug. sessionStorage is scoped to a single tab/browsing context, so
// two tabs (or two browser windows) each keep their own independent session,
// matching how the backend actually isolates data by user_id.
const USER_KEY = "audito_user";
const TOKEN_KEY = "audito_token";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const persist = (u: User | null, token?: string) => {
    setUser(u);
    if (u) {
      window.sessionStorage.setItem(USER_KEY, JSON.stringify(u));
      if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
    } else {
      window.sessionStorage.removeItem(USER_KEY);
      window.sessionStorage.removeItem(TOKEN_KEY);
    }
  };

  // On mount: hydrate from this tab's sessionStorage, then verify the token
  // is still valid against the backend (catches expired/tampered tokens).
  useEffect(() => {
    const rawUser = window.sessionStorage.getItem(USER_KEY);
    const token = window.sessionStorage.getItem(TOKEN_KEY);

    if (!rawUser || !token) {
      setIsLoading(false);
      return;
    }

    try {
      setUser(JSON.parse(rawUser) as User);
    } catch {
      // corrupted cache entry, ignore
    }

    authService
      .me()
      .then((res) => persist(res.data))
      .catch(() => persist(null)) // token expired/invalid -> force logout
      .finally(() => setIsLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await authService.login(email, password);
    persist(res.data.user, res.data.token);
  }, []);

  const register = useCallback(async (name: string, email: string, password: string) => {
    const res = await authService.register(name, email, password);
    persist(res.data.user, res.data.token);
  }, []);

  const logout = useCallback(() => {
    authService.logout().catch(() => {
      // stateless JWT — nothing server-side to invalidate; ignore network errors
    });
    persist(null);
  }, []);

  const updateProfile = useCallback((patch: Partial<User>) => {
    setUser((prev) => {
      if (!prev) return prev;
      const next = { ...prev, ...patch };
      window.sessionStorage.setItem(USER_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        login,
        register,
        logout,
        updateProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
