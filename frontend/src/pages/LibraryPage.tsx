import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type BookSummary, type ImportJob } from "@/lib/api";

export function LibraryPage() {
  const [books, setBooks] = useState<BookSummary[] | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(() => {
    api.books().then((r) => setBooks(r.books)).catch(() => setBooks([]));
  }, []);

  useEffect(refresh, [refresh]);

  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed") return;
    const t = window.setInterval(() => {
      api.job(job.id).then(setJob).catch(() => undefined);
    }, 400);
    return () => window.clearInterval(t);
  }, [job]);

  useEffect(() => {
    if (job?.status === "succeeded") refresh();
  }, [job, refresh]);

  const send = async (file: File) => {
    setBusy(true);
    try {
      const started = await api.upload(file);
      setJob(started);
    } catch (e) {
      setJob({
        id: "local",
        book_id: null,
        filename: file.name,
        status: "failed",
        progress: 0,
        stages: [],
        messages: [],
        logs: [String(e)],
        error: String(e),
        reuse_existing_book_id: null,
      });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    await api.deleteBook(id);
    refresh();
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Your chessbooks</h1>
        <div className="sub">
          Everything below was processed on this machine. No account, no keys, no uploads.
        </div>
      </div>

      <div
        className={`drop ${over ? "over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void send(file);
        }}
      >
        <h3>Drop a chess book PDF here</h3>
        <p className="sub">
          Games, variations, commentary and diagrams are extracted locally, then validated move by
          move against real chess rules.
        </p>
        <button className="primary" disabled={busy} onClick={() => fileRef.current?.click()}>
          Choose a file
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void send(f);
            e.target.value = "";
          }}
        />
        {busy && <p className="tiny" style={{ marginTop: 12 }}>uploading…</p>}
      </div>

      {job && <ImportPanel job={job} onDismiss={() => setJob(null)} />}

      <h3 style={{ margin: "30px 0 12px", fontSize: 13, letterSpacing: "0.08em", color: "var(--text-3)", textTransform: "uppercase" }}>
        Library
      </h3>
      {books === null && <div className="empty">Loading…</div>}
      {books?.length === 0 && (
        <div className="empty">No books yet. Import a PDF to get started.</div>
      )}
      <div className="cards">
        {books?.map((b) => (
          <div className="card" key={b.id}>
            <h3>{b.title}</h3>
            <div className="meta">
              {b.source_filename} · {b.metrics.pages} pages
              {!b.has_text_layer && " · scanned"}
              {b.metrics.ocr_used && ` · OCR on ${b.metrics.ocr_pages} pages`}
            </div>
            <div className="stat-row">
              <Stat n={b.metrics.games} label="games" />
              <Stat n={b.metrics.moves} label="moves" />
              <Stat n={b.metrics.variations} label="variations" />
              <Stat n={b.metrics.board_diagrams} label="diagrams" />
            </div>
            <div className="actions">
              <Link to={`/book/${b.id}`}>
                <button className="primary">Open</button>
              </Link>
              <a href={api.exportPgnUrl(b.id)} download>
                <button>PGN</button>
              </a>
              <div style={{ marginLeft: "auto" }}>
                {b.metrics.errors > 0 ? (
                  <span className="badge bad">{b.metrics.errors} issues</span>
                ) : (
                  <span className="badge ok">clean</span>
                )}
              </div>
              <button className="ghost" title="Remove this book" onClick={() => remove(b.id)}>
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <div className="stat">
      <b>{n.toLocaleString()}</b>
      <span>{label}</span>
    </div>
  );
}

function ImportPanel({ job, onDismiss }: { job: ImportJob; onDismiss: () => void }) {
  const done = job.stages.filter((s) => s.status === "done").length;
  return (
    <div className="summary-box">
      <div className="spread">
        <h3>{job.filename}</h3>
        <span className={`badge ${job.status === "failed" ? "bad" : job.status === "succeeded" ? "ok" : "accent"}`}>
          {job.status}
        </span>
      </div>

      <div className="progress">
        <div style={{ width: `${Math.round(job.progress * 100)}%` }} />
      </div>

      {job.status !== "queued" && (
        <div className="stages">
          {job.stages.map((s) => (
            <div className={`stage ${s.status}`} key={s.key}>
              <span className="dot" />
              <span>{s.label}</span>
              <span className="detail">
                {s.detail || (s.status === "skipped" ? "not needed" : "")}
              </span>
            </div>
          ))}
        </div>
      )}

      {job.error && (
        <p className="tiny" style={{ color: "var(--bad)", marginTop: 12 }}>
          {job.error}
        </p>
      )}
      {job.logs.length > 0 && job.status === "failed" && (
        <pre className="mono tiny" style={{ whiteSpace: "pre-wrap" }}>
          {job.logs.join("\n")}
        </pre>
      )}

      {job.messages.length > 0 && (
        <ul className="tiny" style={{ marginTop: 12, paddingLeft: 18 }}>
          {job.messages.map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      )}

      <div className="rowflex" style={{ marginTop: 14 }}>
        {job.status === "succeeded" && job.book_id && (
          <Link to={`/book/${job.book_id}`}>
            <button className="primary">
              {job.reuse_existing_book_id ? "Open existing book" : "Open book"}
            </button>
          </Link>
        )}
        <span className="tiny">
          {done}/{job.stages.length} stages
        </span>
        <div style={{ marginLeft: "auto" }}>
          <button className="ghost" onClick={onDismiss}>
            dismiss
          </button>
        </div>
      </div>
    </div>
  );
}