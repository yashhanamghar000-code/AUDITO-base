import { useCallback, useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { useChat } from "@/contexts/ChatContext";
import { toast } from "sonner";

export function UploadCard() {
  const { uploadDocument } = useChat();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleFiles = useCallback(
    async (files: FileList | null) => {
      if (!files) return;
      for (const file of Array.from(files)) {
        try {
          await uploadDocument(file);
        } catch (err) {
          toast.error(err instanceof Error ? err.message : "Upload failed");
        }
      }
    },
    [uploadDocument],
  );

  return (
    <motion.div
      whileHover={{ scale: 1.01 }}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        handleFiles(e.dataTransfer.files);
      }}
      className={cn(
        "cursor-pointer rounded-xl border border-dashed border-border bg-card/50 p-4 text-center transition-colors",
        dragOver && "border-foreground bg-accent",
      )}
      role="button"
      tabIndex={0}
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        multiple
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />
      <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-muted">
        <UploadCloud className="h-5 w-5" />
      </div>
      <div className="text-sm font-medium">Upload PDF</div>
      <div className="text-[11px] text-muted-foreground">Drag &amp; drop supported</div>
    </motion.div>
  );
}
