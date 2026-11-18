import { MessageCircle, X } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "./api";

export type MapContext = { kind: string; record_id: string };

export type PublishedContext =
  | { kind: "forecast"; store: string; product: string }
  | { kind: "coverage"; store: string; product: string; increase: number }
  | { kind: "lateness"; vendor?: string }
  | { kind: "shipment"; record_id: number };

type Answer = {
  answer: string;
  model: string;
  citations: { label: string; analysis_id: string }[];
};

export function Chat({
  analysisId,
  published,
  mapRecord,
  onEvidence,
}: {
  analysisId?: string;
  published?: PublishedContext;
  mapRecord?: MapContext;
  onEvidence: (id: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const active = useRef<AbortController | null>(null);
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<{ question: string; response: Answer }[]>(
    [],
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

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
    try {
      const response = await api<Answer>(
        "/chat",
        {
          message: question,
          previous: turns.slice(-2).map((turn) => turn.question),
          analysis_id: analysisId,
          published,
          map_record: mapRecord,
        },
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setTurns((current) => [...current.slice(-5), { question, response }]);
        setMessage("");
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError((failure as Error).message);
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
        <p>
          Ask about the selected result, its limits, or how to use the
          workspace.
        </p>
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
              <p className="chat-answer">{response.answer}</p>
              {response.citations.map(({ label, analysis_id }) => (
                <button
                  key={analysis_id}
                  onClick={() => {
                    dialog.current?.close();
                    onEvidence(analysis_id);
                  }}
                >
                  {label}
                </button>
              ))}
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
            {error ? "Retry message" : "Send message"}
          </button>
          {pending && (
            <button
              type="button"
              onClick={() => {
                cancel();
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
