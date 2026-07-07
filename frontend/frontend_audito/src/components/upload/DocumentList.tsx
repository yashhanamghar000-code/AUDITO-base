import { FileText, X, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useChat } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";
import type { DocStatus, StageStatus, UploadedDoc } from "@/types";

const STATUS_STYLES: Record<DocStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  processing: "bg-blue-500/15 text-blue-500 dark:text-blue-400",
  indexed: "bg-green-500/15 text-green-600 dark:text-green-400",
  failed: "bg-destructive/15 text-destructive",
};

const STATUS_LABEL: Record<DocStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  indexed: "Indexed",
  failed: "Failed",
};

const STAGE_DOT: Record<StageStatus, string> = {
  waiting: "bg-muted-foreground/40",
  processing: "bg-blue-500 animate-pulse",
  done: "bg-green-500",
  failed: "bg-destructive",
};

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DocItem({ doc }: { doc: UploadedDoc }) {
  const { removeDocument } = useChat();
  const [open, setOpen] = useState(doc.status !== "indexed");
  const uploading = doc.progress < 100;

  return (
    <div className="rounded-lg border border-border bg-card/50 p-2.5">
      <div className="flex items-start gap-2">
        <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1 truncate text-xs font-medium">{doc.name}</div>
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={() => removeDocument(doc.id)}
              aria-label="Remove"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
            <span>{formatSize(doc.size)}</span>
            <span>·</span>
            <span
              className={cn(
                "rounded-full px-1.5 py-0.5 font-medium",
                STATUS_STYLES[doc.status],
              )}
            >
              {STATUS_LABEL[doc.status]}
            </span>
          </div>

          {uploading && (
            <div className="mt-2 space-y-1">
              <Progress value={doc.progress} className="h-1" />
              <div className="text-[10px] text-muted-foreground">
                Uploading… {doc.progress}%
              </div>
            </div>
          )}

          {!uploading && (
            <button
              onClick={() => setOpen((v) => !v)}
              className="mt-1 flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
            >
              {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              Parsing pipeline
            </button>
          )}

          <AnimatePresence initial={false}>
            {open && !uploading && (
              <motion.ul
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="mt-1.5 space-y-1 overflow-hidden text-[11px]"
              >
                {doc.stages.map((s) => (
                  <li key={s.key} className="flex items-center gap-2">
                    <span className={cn("h-1.5 w-1.5 rounded-full", STAGE_DOT[s.status])} />
                    <span
                      className={cn(
                        s.status === "done" && "text-foreground",
                        s.status === "processing" && "text-blue-500 dark:text-blue-400",
                        s.status === "waiting" && "text-muted-foreground",
                        s.status === "failed" && "text-destructive",
                      )}
                    >
                      {s.label}
                    </span>
                  </li>
                ))}
              </motion.ul>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

export function DocumentList() {
  const { documents } = useChat();
  if (documents.length === 0) return null;
  return (
    <div className="mt-3 space-y-2">
      <div className="px-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Documents
      </div>
      <div className="space-y-2">
        {documents.map((d) => (
          <DocItem key={d.id} doc={d} />
        ))}
      </div>
    </div>
  );
}
