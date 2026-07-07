import { createFileRoute, Outlet, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { ChatLayout } from "@/components/layout/ChatLayout";

export const Route = createFileRoute("/_authenticated")({
  component: AuthenticatedLayout,
});

function AuthenticatedLayout() {
  const { isAuthenticated, isLoading } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    // Don't redirect until the initial token-verification round trip
    // (AuthContext calling /api/auth/me) has actually finished — otherwise a
    // real, valid session gets bounced to /login for a flash on every reload.
    if (!isLoading && !isAuthenticated) {
      navigate({ to: "/auth/login", replace: true });
    }
  }, [isLoading, isAuthenticated, navigate]);

  if (isLoading || !isAuthenticated) return null;
  return (
    <ChatLayout>
      <Outlet />
    </ChatLayout>
  );
}
