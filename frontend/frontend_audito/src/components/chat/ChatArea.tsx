import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { AuditoLogo } from "@/components/brand/AuditoLogo";
import { ChatInput } from "@/components/chat/ChatInput";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { useChat } from "@/contexts/ChatContext";

export function ChatArea() {
  const { activeConversation, isStreaming } = useChat();
  const bottomRef = useRef<HTMLDivElement>(null);
  const messages = activeConversation?.messages ?? [];

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, isStreaming, messages]);

  const empty = messages.length === 0;

  return (
    <div className="relative flex h-full flex-col">
      {empty ? (
        <div className="flex flex-1 flex-col items-center justify-center px-4 pb-40">
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35 }}
            className="flex flex-col items-center"
          >
            <AuditoLogo className="h-16 w-16" />
            <h1 className="mt-5 text-2xl font-semibold tracking-tight md:text-3xl">
              AUDITO AI
            </h1>
            <p className="mt-2 text-sm text-muted-foreground md:text-base">
              What would you like to audit today?
            </p>
          </motion.div>
          <div className="mt-8 w-full max-w-2xl">
            <ChatInput />
          </div>
        </div>
      ) : (
        <>
          <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-8 pb-40">
              {messages.map((m) => (
                <MessageBubble key={m.id} message={m} />
              ))}
              <div ref={bottomRef} />
            </div>
          </div>
          <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-background via-background/95 to-transparent pt-8">
            <div className="pointer-events-auto mx-auto w-full max-w-3xl px-4 pb-4">
              <ChatInput />
              <p className="mt-2 text-center text-[10px] text-muted-foreground">
                AUDITO AI can make mistakes. Verify important information.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
