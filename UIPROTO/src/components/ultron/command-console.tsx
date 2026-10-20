/**
 * The command console: the console's one input, wired to `POST /chat/stream`.
 *
 * The answer is rendered token-by-token as it arrives, and each tool call is
 * shown as it starts and finishes, so a long tool-using turn never looks hung.
 * When a turn completes, the task and agent lists are invalidated: a chat turn is
 * allowed to spawn agents or queue tasks through tools, and the panels should
 * reflect that without waiting for the next poll.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { ArrowUpRight, Square, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError, streamChat } from "@/lib/api";
import { broadcastUnauthorized } from "@/lib/auth";
import { queryKeys } from "@/lib/queries";

type Role = "user" | "assistant" | "tool";

interface Message {
  id: number;
  role: Role;
  text: string;
  meta?: string;
  pending?: boolean;
}

export function CommandConsole({ onChat }: { onChat?: (prompt: string) => void } = {}) {
  const queryClient = useQueryClient();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);
  const controller = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const onChatRef = useRef(onChat);
  onChatRef.current = onChat;

  const scroll = () => {
    requestAnimationFrame(() => {
      const node = logRef.current;
      if (node) node.scrollTop = node.scrollHeight;
    });
  };

  const patch = (id: number, update: (message: Message) => Message) =>
    setMessages((current) => current.map((message) => (message.id === id ? update(message) : message)));

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const prompt = input.trim();
    if (!prompt || busy) return;
    onChatRef.current?.(prompt);
    setInput("");
    setBusy(true);

    const userId = nextId.current++;
    const replyId = nextId.current++;
    setMessages((current) => [
      ...current,
      { id: userId, role: "user", text: prompt },
      { id: replyId, role: "assistant", text: "", pending: true },
    ]);
    scroll();

    controller.current = new AbortController();
    try {
      await streamChat(
        prompt,
        {
          token: ({ text }) => {
            patch(replyId, (message) => ({ ...message, text: message.text + text }));
            scroll();
          },
          tool_started: ({ tool }) => {
            const toolId = nextId.current++;
            setMessages((current) => [
              ...current,
              { id: toolId, role: "tool", text: `${tool} started`, pending: true },
            ]);
            scroll();
          },
          tool_completed: ({ tool, ok }) => {
            setMessages((current) =>
              current.map((message) =>
                message.role === "tool" && message.text === `${tool} started`
                  ? { ...message, text: `${tool} ${ok ? "completed" : "failed"}`, pending: false }
                  : message,
              ),
            );
          },
          done: ({ answer, model, tool_calls, took_ms }) => {
            patch(replyId, (message) => ({
              ...message,
              text: message.text || answer,
              pending: false,
              meta: `${model} · ${tool_calls} tool call(s) · ${took_ms} ms`,
            }));
            void queryClient.invalidateQueries({ queryKey: queryKeys.tasks });
            void queryClient.invalidateQueries({ queryKey: queryKeys.agents });
            scroll();
          },
          error: ({ message }) => {
            patch(replyId, (m) => ({ ...m, text: m.text || "Error", pending: false, meta: message }));
          },
        },
        controller.current.signal,
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) broadcastUnauthorized();
      else if ((error as { name?: string }).name !== "AbortError") {
        patch(replyId, (message) => ({
          ...message,
          pending: false,
          meta: error instanceof Error ? error.message : "stream failed",
        }));
      }
    } finally {
      patch(replyId, (message) => ({ ...message, pending: false }));
      setBusy(false);
      controller.current = null;
    }
  }

  return (
    <div className="command-search console-shell">
      {messages.length ? (
        <div className="console-transcript" ref={logRef} aria-live="polite">
          {messages.map((message) => (
            <div key={message.id} className={`console-line console-${message.role}`}>
              <span className="console-prompt">
                {message.role === "user" ? ">" : message.role === "tool" ? "#" : "◊"}
              </span>
              <span className="console-text">{message.text || (message.pending ? "…" : "")}</span>
              {message.meta ? <span className="console-meta">{message.meta}</span> : null}
            </div>
          ))}
        </div>
      ) : null}
      <form onSubmit={send}>
        <Terminal />
        <input
          aria-label="Ask Ultron"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask Ultron anything…"
          disabled={busy}
        />
        {busy ? (
          <Button
            variant="ghost"
            size="icon"
            type="button"
            aria-label="Stop"
            title="Stop"
            onClick={() => controller.current?.abort()}
          >
            <Square />
          </Button>
        ) : (
          <Button variant="ghost" size="icon" type="submit" aria-label="Send" title="Send">
            <ArrowUpRight />
          </Button>
        )}
      </form>
    </div>
  );
}
