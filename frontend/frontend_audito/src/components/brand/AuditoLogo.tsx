import { cn } from "@/lib/utils";

export function AuditoLogo({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex items-center justify-center rounded-xl bg-foreground text-background shadow-sm",
        className,
      )}
      aria-label="AUDITO AI"
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-[60%] w-[60%]"
      >
        <path d="M12 2L4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z" />
        <path d="M9 12l2 2 4-4" />
      </svg>
    </div>
  );
}

export function AuditoWordmark({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <AuditoLogo className="h-8 w-8" />
      <div className="leading-tight">
        <div className="text-sm font-semibold tracking-tight">AUDITO</div>
        <div className="text-[10px] font-medium uppercase tracking-[0.2em] text-muted-foreground">
          AI
        </div>
      </div>
    </div>
  );
}
