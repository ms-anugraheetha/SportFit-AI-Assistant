import { FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, getMeta, sendChat } from "./api";
import type { Meta, SourceRef } from "./types";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  sources?: SourceRef[];
  safety?: string[];
  reference?: string | null;
  degraded?: boolean;
  isError?: boolean;
}

const FALLBACK_DISCLAIMER =
  "SportFit AI Assistant is an independent academic prototype. It is not affiliated with, endorsed by, or connected to SportFit, and requests created here are demonstration data only.";
const FALLBACK_PROMPTS = [
  "What repair services are available?",
  "I need help with a treadmill.",
  "Submit a repair request.",
  "Track my demo request.",
  "I have a safety concern.",
];
const WELCOME =
  "Hi, I'm the SportFit AI Assistant demo. I can answer questions about fitness-equipment services, help you record a demonstration repair request, look up a demonstration request reference, and share safety guidance. I can't change any request's status.";

const STORAGE_KEY = "sportfit_conversation_id";

function loadConversationId(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}
function saveConversationId(id: string) {
  try {
    sessionStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* storage can be unavailable (private mode); the chat still works for this page view */
  }
}

export default function ChatPage() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [messages, setMessages] = useState<Message[]>([{ id: 0, role: "assistant", text: WELCOME }]);
  const [actions, setActions] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastSent, setLastSent] = useState<string | null>(null);
  const conversationId = useRef<string | null>(loadConversationId());
  const nextId = useRef(1);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    getMeta().then(setMeta).catch(() => setMeta(null));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, busy]);

  const disclaimer = meta?.disclaimer ?? FALLBACK_DISCLAIMER;
  const prompts = meta?.suggested_prompts ?? FALLBACK_PROMPTS;

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setLastSent(trimmed);
    setActions([]);
    setMessages((m) => [...m, { id: nextId.current++, role: "user", text: trimmed }]);
    setInput("");
    try {
      const res = await sendChat(conversationId.current, trimmed);
      conversationId.current = res.conversation_id;
      saveConversationId(res.conversation_id);
      setMessages((m) => [
        ...m,
        {
          id: nextId.current++,
          role: "assistant",
          text: res.reply,
          sources: res.sources,
          safety: res.safety_notice,
          reference: res.request_reference,
          degraded: res.degraded,
        },
      ]);
      setActions(res.suggested_actions);
      setLastSent(null);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
      setMessages((m) => [...m, { id: nextId.current++, role: "assistant", text: msg, isError: true }]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send(input);
  }

  function newConversation() {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
    conversationId.current = null;
    setMessages([{ id: nextId.current++, role: "assistant", text: WELCOME }]);
    setActions([]);
    setLastSent(null);
  }

  return (
    <div className="page">
      <div className="banner" role="note">
        <strong>Independent academic prototype.</strong> {disclaimer}
      </div>

      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 32 32" width="28" height="28">
              <path d="M7 12v8M11 9v14M21 9v14M25 12v8M11 16h10" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" fill="none" />
            </svg>
          </span>
          <div>
            <h1>SportFit AI Assistant</h1>
            <p className="muted">Demo chat for fitness-equipment service questions</p>
          </div>
        </div>
        <nav className="topbar-actions" aria-label="Chat tools">
          <button type="button" className="btn btn-ghost" onClick={() => void send("Talk to a human")} disabled={busy}>
            Talk to a human
          </button>
          <button type="button" className="btn btn-ghost" onClick={newConversation} disabled={busy}>
            New chat
          </button>
        </nav>
      </header>

      <main className="chat-shell">
        <section className="chat" aria-label="Chat with the assistant">
          <div className="messages" role="log" aria-live="polite" aria-relevant="additions" tabIndex={0}>
            {messages.map((m) => (
              <MessageBubble key={m.id} m={m} />
            ))}
            {busy && (
              <div className="row assistant" aria-live="polite">
                <div className="bubble typing">Assistant is thinking…</div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          {(actions.length > 0 || messages.length === 1) && !busy && (
            <div className="chips" aria-label="Suggested replies">
              {(messages.length === 1 ? prompts : actions).map((a) => (
                <button key={a} type="button" className="chip" onClick={() => void send(a)}>
                  {a}
                </button>
              ))}
            </div>
          )}

          {lastSent && !busy && messages[messages.length - 1]?.isError && (
            <div className="retry">
              <button type="button" className="btn btn-secondary" onClick={() => void send(lastSent)}>
                Try sending again
              </button>
            </div>
          )}

          <form className="composer" onSubmit={onSubmit}>
            <label htmlFor="chat-input" className="sr-only">
              Your message
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              maxLength={1000}
              rows={2}
              placeholder="Type your question…"
              disabled={busy}
            />
            <button type="submit" className="btn btn-primary" disabled={busy || !input.trim()}>
              Send
            </button>
          </form>
          <p className="privacy">
            Privacy: this is a demo. Please don't share passwords, payment details, ID numbers or other sensitive information.
            Use made-up details if you only want to try the repair-request flow.
          </p>
        </section>
      </main>

      <footer className="footer">
        <span>Not affiliated with SportFit. Answers come from a small curated knowledge base and may be incomplete.</span>
        <a href="/staff">Staff sign in</a>
      </footer>
    </div>
  );
}

function MessageBubble({ m }: { m: Message }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    if (!m.reference) return;
    try {
      await navigator.clipboard.writeText(m.reference);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard may be blocked; the reference is still visible to copy by hand */
    }
  }
  return (
    <div className={`row ${m.role}`}>
      <div className={`bubble ${m.role}${m.isError ? " error" : ""}`}>
        {m.safety && m.safety.length > 0 && (
          <div className="safety" role="note">
            <strong>Safety first</strong>
            <ul>
              {m.safety.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
        )}
        <p className="text">{m.text}</p>
        {m.reference && (
          <div className="reference" aria-label="Demonstration request reference">
            <span className="muted">Demo reference</span>
            <code>{m.reference}</code>
            <button type="button" className="btn btn-ghost small" onClick={() => void copy()}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        )}
        {m.degraded && <p className="muted small">The AI model is unavailable right now, so this answer comes from the verified knowledge base only.</p>}
        {m.sources && m.sources.length > 0 && (
          <p className="sources small">
            Sources:{" "}
            {m.sources.map((s, i) => (
              <span key={s.url}>
                {i > 0 && ", "}
                <a href={s.url} target="_blank" rel="noopener noreferrer">
                  {s.title}
                </a>
              </span>
            ))}
          </p>
        )}
      </div>
    </div>
  );
}
