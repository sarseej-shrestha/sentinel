import { MessageCircle, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";

export type MapContext = { kind: string; record_id: string; map_id?: string };

export type PublishedContext =
  | { kind: "forecast"; store: string; product: string }
  | { kind: "coverage"; store: string; product: string; increase: number }
  | { kind: "lateness"; vendor?: string }
  | { kind: "shipment"; record_id: number };

export type ChatScope = {
  label: string;
  dataset?: string | null;
  analysis_id?: string | null;
  evidence_id?: string | null;
  target_id?: string | null;
  version?: string | null;
  source_id?: string | null;
};

export type ChatCitation = {
  label: string;
  scope: ChatScope;
  href?: string | null;
};

type Answer = {
  answer: string;
  model: string;
  scope?: ChatScope;
  verified?: string[];
  notice?: string | null;
  citations: ChatCitation[];
};

export function Chat({
  workspace,
  analysisId,
  targetId,
  published,
  mapRecord,
  contextLabel,
  onEvidence,
}: {
  workspace: "workspace" | "map" | "history" | "published";
  analysisId?: string;
  targetId?: string | null;
  published?: PublishedContext;
  mapRecord?: MapContext;
  contextLabel: string;
  onEvidence: (citation: ChatCitation) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const active = useRef<AbortController | null>(null);
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<
    { question: string; response: Answer; scopeKey: string }[]
  >([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [retryable, setRetryable] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const scopeKey = JSON.stringify([
    workspace,
    analysisId,
    targetId,
    published,
    mapRecord,
  ]);
  const latestScope = useRef(scopeKey);
  latestScope.current = scopeKey;
  useEffect(() => {
    setPending(false);
    setError("");
    setCancelled(false);
    return () => {
      active.current?.abort();
      active.current = null;
    };
  }, [scopeKey]);

  function cancel() {
    active.current?.abort();
    active.current = null;
    setPending(false);
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    const question = message.trim();
    if (!question || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    setError("");
    setCancelled(false);
    try {
      const response = await api<Answer>(
        "/chat",
        {
          message: question,
          history: turns
            .filter(
              (turn) => turn.scopeKey === scopeKey && !turn.response.notice,
            )
            .slice(-4)
            .map((turn) => ({
              question: turn.question,
              answer: turn.response.answer.slice(0, 1000),
            })),
          workspace,
          analysis_id: analysisId,
          target_id: analysisId ? targetId : undefined,
          published,
          map_record: mapRecord,
        },
        controller.signal,
      );
      if (!controller.signal.aborted && latestScope.current === scopeKey) {
        setTurns((current) => [
          ...current.slice(-9),
          { question, response, scopeKey },
        ]);
        setMessage("");
      }
    } catch (failure) {
      if (!controller.signal.aborted && latestScope.current === scopeKey) {
        setError((failure as Error).message);
        setRetryable(
          failure instanceof ApiError &&
            (failure.status === 0 || failure.status >= 500),
        );
      }
    } finally {
      if (active.current === controller) {
        active.current = null;
        setPending(false);
      }
    }
  }

  return (
    <>
      <button
        className="chat-trigger primary"
        ref={trigger}
        type="button"
        aria-haspopup="dialog"
        onClick={() => {
          dialog.current?.showModal();
          input.current?.focus();
        }}
      >
        <MessageCircle size={19} aria-hidden="true" /> Ask Qwen
      </button>
      <dialog
        ref={dialog}
        className="chat-dialog"
        aria-labelledby="chat-heading"
        onClose={() => {
          cancel();
          trigger.current?.focus();
        }}
      >
        <header>
          <h2 id="chat-heading">Sentinel assistant</h2>
          <button
            aria-label="Close assistant"
            onClick={() => dialog.current?.close()}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <p>Ask about Sentinel, supply-chain concepts, or the current result.</p>
        <div className="chat-context">
          <span>Context · {contextLabel}</span>
          <button
            type="button"
            onClick={() => {
              cancel();
              setTurns([]);
              setMessage("");
              setError("");
              setCancelled(false);
              input.current?.focus();
            }}
          >
            New conversation
          </button>
        </div>
        <div
          className="chat-turns"
          role="region"
          aria-label="Conversation"
          tabIndex={turns.length ? 0 : undefined}
        >
          {turns.map(({ question, response }, index) => (
            <article key={index}>
              <strong>You</strong>
              <p>{question}</p>
              <strong>{response.model}</strong>
              {response.scope && (
                <p className="fine">Discussed · {response.scope.label}</p>
              )}
              <p className="chat-answer">{response.answer}</p>
              {response.notice && (
                <p className="phase4-error">{response.notice}</p>
              )}
              {!!response.verified?.length && (
                <div className="chat-facts">
                  <strong>Verified source details</strong>
                  {response.verified.map((fact) => (
                    <p className="chat-answer" key={fact}>
                      {fact}
                    </p>
                  ))}
                </div>
              )}
              {response.citations.map((citation, i) =>
                citation.href ? (
                  <a
                    key={i}
                    href={citation.href}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {citation.label}
                  </a>
                ) : (
                  <button
                    key={i}
                    onClick={() => {
                      dialog.current?.close();
                      onEvidence(citation);
                    }}
                  >
                    {citation.label}
                  </button>
                ),
              )}
            </article>
          ))}
        </div>
        <form onSubmit={send}>
          <label htmlFor="chat-message">Message</label>
          <textarea
            id="chat-message"
            ref={input}
            value={message}
            maxLength={600}
            readOnly={pending}
            rows={3}
            onChange={(event) => setMessage(event.target.value)}
          />
          <p role="status">
            {pending
              ? "Waiting for Qwen…"
              : cancelled
                ? "Request cancelled. Your message is still here."
                : turns.length && !error && !message
                  ? "Response received."
                  : ""}
          </p>
          {error && (
            <p className="phase4-error" role="alert">
              {error}
            </p>
          )}
          <button
            className="primary"
            type="submit"
            aria-disabled={pending || !message.trim()}
          >
            {error && retryable ? "Retry message" : "Send message"}
          </button>
          {pending && (
            <button
              type="button"
              onClick={() => {
                cancel();
                setCancelled(true);
                input.current?.focus();
              }}
            >
              Cancel request
            </button>
          )}
        </form>
      </dialog>
    </>
  );
}
