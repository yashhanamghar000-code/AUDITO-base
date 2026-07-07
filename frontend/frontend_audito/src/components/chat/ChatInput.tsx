import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { motion } from "framer-motion";
import { ArrowUp, Paperclip, Mic, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChat } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export function ChatInput() {
  const { sendMessage, isStreaming, stopGeneration, uploadDocument } = useChat();
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 220) + "px";
  }, [value]);

  async function submit() {
    const text = value.trim();
    if (!text || isStreaming) return;
    setValue("");
    await sendMessage(text);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const canSend = value.trim().length > 0 && !isStreaming;

  return (
    <div className="relative">
      <input
        ref={fileRef}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (!file) return;
          try {
            await uploadDocument(file);
            toast.success("Upload started");
          } catch (err) {
            toast.error(err instanceof Error ? err.message : "Upload failed");
          }
        }}
      />
      <div className="flex items-end gap-2 rounded-3xl border border-border bg-card p-2.5 shadow-sm transition-shadow focus-within:shadow-md">
        <Button
          variant="ghost"
          size="icon"
          className="h-9 w-9 shrink-0 rounded-full"
          onClick={() => fileRef.current?.click()}
          aria-label="Attach PDF"
        >
          <Paperclip className="h-4 w-4" />
        </Button>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKey}
          rows={1}
          placeholder="Ask AUDITO AI anything..."
          className={cn(
            "min-h-[36px] flex-1 resize-none bg-transparent px-1 py-2 text-sm leading-6 outline-none placeholder:text-muted-foreground",
          )}
        />
        <Button
          variant="ghost"
          size="icon"
          className="h-9 w-9 shrink-0 rounded-full"
          disabled
          aria-label="Voice (coming soon)"
        >
          <Mic className="h-4 w-4" />
        </Button>

        {isStreaming ? (
          <Button
            size="icon"
            variant="default"
            className="h-9 w-9 shrink-0 rounded-full"
            onClick={stopGeneration}
            aria-label="Stop generating"
          >
            <Square className="h-3.5 w-3.5" fill="currentColor" />
          </Button>
        ) : (
          <motion.button
            whileTap={{ scale: 0.9 }}
            disabled={!canSend}
            onClick={submit}
            aria-label="Send"
            className={cn(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors",
              canSend
                ? "bg-primary text-primary-foreground hover:opacity-90"
                : "cursor-not-allowed bg-muted text-muted-foreground",
            )}
          >
            <ArrowUp className="h-4 w-4" />
          </motion.button>
        )}
      </div>
    </div>
  );
}
