import { motion } from "framer-motion";
import { useState } from "react";
import {
  Copy,
  Check,
  ThumbsUp,
  ThumbsDown,
  RefreshCw,
  Share2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeHighlight from "rehype-highlight";
import { AuditoLogo } from "@/components/brand/AuditoLogo";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { useChat } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";
import type { Message } from "@/types";
import { toast } from "sonner";

function CodeBlock({ className, children }: { className?: string; children?: React.ReactNode }) {
  const language = /language-(\w+)/.exec(className || "")?.[1] ?? "text";
  const [copied, setCopied] = useState(false);
  const code = String(children ?? "").replace(/\n$/, "");

  async function copy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-border bg-[#0d1117] text-[#c9d1d9]">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-1.5 text-[11px]">
        <span className="uppercase tracking-wider text-white/60">{language}</span>
        <button
          onClick={copy}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-white/70 hover:bg-white/10 hover:text-white"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 text-[13px] leading-relaxed">
        <code className={className}>{code}</code>
      </pre>
    </div>
  );
}

function UserAvatar() {
  const { user } = useAuth();
  const initial = (user?.name ?? "U").slice(0, 1).toUpperCase();
  return (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-foreground text-xs font-semibold text-background">
      {initial}
    </div>
  );
}

export function MessageBubble({ message, isLast }: { message: Message; isLast?: boolean }) {
  const { regenerate, reactMessage, isStreaming, sendMessage } = useChat();
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);

  async function copyAll() {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className={cn("flex w-full gap-3", isUser ? "flex-row-reverse" : "flex-row")}
    >
      {isUser ? (
        <UserAvatar />
      ) : (
        <AuditoLogo className="h-8 w-8 shrink-0" />
      )}

      <div className={cn("flex min-w-0 max-w-[85%] flex-col", isUser ? "items-end" : "items-start")}>
        <div
          className={cn(
            "prose-chat rounded-2xl px-4 py-2.5 text-sm",
            isUser
              ? "bg-primary text-primary-foreground"
              : "bg-transparent text-foreground",
          )}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap">{message.content}</div>
          ) : message.content ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[rehypeKatex, rehypeHighlight]}
              components={{
                code: ({ inline, className, children, ...props }: any) =>
                  inline ? (
                    <code className={className} {...props}>
                      {children}
                    </code>
                  ) : (
                    <CodeBlock className={className}>{children}</CodeBlock>
                  ),
                a: ({ node, ...props }) => (
                  <a {...props} target="_blank" rel="noreferrer noopener" />
                ),
              }}
            >
              {message.content}
            </ReactMarkdown>
          ) : (
            <TypingDots />
          )}
        </div>

        {!isUser && message.content && (
          <div className="mt-1.5 flex items-center gap-0.5 opacity-70 transition-opacity hover:opacity-100">
            <IconAction label="Copy" onClick={copyAll}>
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            </IconAction>
            <IconAction
              label="Like"
              onClick={() => reactMessage(message.id, message.liked === true ? null : true)}
              active={message.liked === true}
            >
              <ThumbsUp className="h-3.5 w-3.5" />
            </IconAction>
            <IconAction
              label="Dislike"
              onClick={() => reactMessage(message.id, message.liked === false ? null : false)}
              active={message.liked === false}
            >
              <ThumbsDown className="h-3.5 w-3.5" />
            </IconAction>
            <IconAction
              label="Regenerate"
              onClick={() => !isStreaming && regenerate(message.id)}
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </IconAction>
            <IconAction
              label="Share"
              onClick={() => {
                navigator.clipboard.writeText(message.content);
                toast.success("Response copied to clipboard");
              }}
            >
              <Share2 className="h-3.5 w-3.5" />
            </IconAction>
          </div>
        )}

        {!isUser && isLast && !isStreaming && !!message.followUps?.length && (
          <div className="mt-3 flex flex-wrap gap-2">
            {message.followUps.map((q, i) => (
              <button
                key={i}
                onClick={() => sendMessage(q)}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-foreground/80 transition-colors hover:border-foreground/40 hover:bg-accent hover:text-foreground"
              >
                {q}
              </button>
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}

function IconAction({
  children,
  label,
  onClick,
  active,
}: {
  children: React.ReactNode;
  label: string;
  onClick?: () => void;
  active?: boolean;
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={onClick}
      aria-label={label}
      className={cn("h-7 w-7 rounded-md", active && "bg-accent text-accent-foreground")}
    >
      {children}
    </Button>
  );
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1 py-1">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="block h-1.5 w-1.5 rounded-full bg-muted-foreground"
          animate={{ opacity: [0.2, 1, 0.2] }}
          transition={{ duration: 1.1, repeat: Infinity, delay: i * 0.15 }}
        />
      ))}
    </div>
  );
}
