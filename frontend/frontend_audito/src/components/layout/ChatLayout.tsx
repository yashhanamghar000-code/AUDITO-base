import { useEffect, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Menu, X, PanelLeft } from "lucide-react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from "@/components/ui/tooltip";
import { AuditoWordmark } from "@/components/brand/AuditoLogo";

const SIDEBAR_STATE_KEY = "audito_sidebar_open";

export function ChatLayout({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  // Device-level UI preference, not per-user data — plain localStorage is
  // fine here (unlike auth/chat data, there's nothing sensitive or
  // user-specific about whether the sidebar happens to be collapsed).
  const [desktopOpen, setDesktopOpen] = useState(() => {
    const stored = window.localStorage.getItem(SIDEBAR_STATE_KEY);
    return stored === null ? true : stored === "true";
  });

  useEffect(() => {
    window.localStorage.setItem(SIDEBAR_STATE_KEY, String(desktopOpen));
  }, [desktopOpen]);

  // Ctrl+. (or Cmd+. on Mac) toggles the sidebar, matching the shortcut
  // shown in Claude's own sidebar-toggle tooltip.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === ".") {
        e.preventDefault();
        setDesktopOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background text-foreground">
      {/* Desktop sidebar — collapses to width 0 instead of unmounting, so
          the slide animation has something to animate from/to. */}
      <motion.aside
        initial={false}
        animate={{ width: desktopOpen ? 300 : 0 }}
        transition={{ type: "spring", damping: 28, stiffness: 260 }}
        className="hidden shrink-0 overflow-hidden border-r border-sidebar-border bg-sidebar md:block"
        style={{ borderRightWidth: desktopOpen ? 1 : 0 }}
      >
        <div className="h-full w-[300px]">
          <Sidebar />
        </div>
      </motion.aside>

      {/* Mobile drawer */}
      <AnimatePresence>
        {mobileOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-40 bg-black/50 md:hidden"
              onClick={() => setMobileOpen(false)}
            />
            <motion.aside
              initial={{ x: "-100%" }}
              animate={{ x: 0 }}
              exit={{ x: "-100%" }}
              transition={{ type: "spring", damping: 24, stiffness: 220 }}
              className="fixed inset-y-0 left-0 z-50 w-[85%] max-w-[320px] border-r border-sidebar-border bg-sidebar md:hidden"
            >
              <Sidebar onNavigate={() => setMobileOpen(false)} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top bar */}
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-3 md:hidden">
          <Button variant="ghost" size="icon" onClick={() => setMobileOpen(true)} aria-label="Open menu">
            {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </Button>
          <AuditoWordmark />
          <div className="w-9" />
        </div>

        {/* Desktop sidebar toggle — sits at the top-left of the main
            content area so it stays reachable even when the sidebar is
            fully collapsed, same placement as Claude's own toggle. */}
        <div className="hidden h-12 shrink-0 items-center px-3 md:flex">
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setDesktopOpen((prev) => !prev)}
                  aria-label={desktopOpen ? "Close sidebar" : "Open sidebar"}
                >
                  <PanelLeft className="h-5 w-5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {desktopOpen ? "Close sidebar" : "Open sidebar"}
                <span className="ml-1.5 text-muted-foreground">Ctrl+.</span>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>        </div>

        <main className="min-h-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
