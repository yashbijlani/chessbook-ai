import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type BookDetail, type GameSummary, type IssueRow } from "@/lib/api";

/** Book overview: structure, games, problems.  Clicking a game opens the reader. */
export function BookPage() {
  const { bookId = "" } = useParams();
  const nav = useNavigate();
  const [book, setBook] = useState<BookDetail | null>(null);
  const [games, setGames] = useState<GameSummary[]>([]);
  const [issues, setIssues] = useState<IssueRow[]>([]);
  const [q, setQ] = useState("");
  const [showIssues, setShowIssues] = useState(false);
  const [showToc, setShowToc] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([api.book(bookId), api.games(bookId), api.issues(bookId)]).then(
      ([b, g, i]) => {
        if (!alive) return;
        setBook(b);
        setGames(g.games);
        setIssues(i.issues);
      },
    );
    return () => {
      alive = false;
    };
  }, [bookId]);

  const chapterTitle = (id: string | null) =>
    book?.chapters.find((c) => c.id.endsWith((id ?? "").replace(/^ch_/, "")))?.title ??
    book?.chapters.find((c) => c.id === id)?.title;

  const filtered = useMemo(() => {
    if (!q.trim()) return games;
    const needle = q.toLowerCase();
    return games.filter((g) =>
      [g.title, g.players.white ?? "", g.players.black ?? "", g.event ?? "", g.opening ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [games, q]);

  if (!book) return <div className="page"><div className="empty">Loading…</div></div>;

  const m = book.metrics;

  return (
    <div className="page">
      <div className="page-head">
        <div className="crumb tiny" style={{ textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-3)" }}>
          <Link to="/library">library</Link> / {book.source_filename}
        </div>
        <h1>{book.title}</h1>
        <div className="sub">
          {m.pages} pages · {m.chapters} chapters · recovery level {m.recovery_level}
          {!book.has_text_layer && " · scanned document, OCR used"}
        </div>
      </div>

      <div className="cards" style={{ marginBottom: 22 }}>
        <Metric n={m.games} label="games" />
        <Metric n={m.moves} label="moves" />
        <Metric n={m.variations} label="variations" />
        <Metric n={m.comments} label="comments" />
        <Metric n={m.board_diagrams} label="diagrams" />
        <Metric n={m.errors} label="errors" tone={m.errors ? "bad" : "ok"} />
        <Metric n={m.warnings} label="warnings" tone={m.warnings ? "warn" : "ok"} />
        <Metric n={m.nodes} label="nodes" />
      </div>

      <div className="rowflex" style={{ marginBottom: 14 }}>
        <input
          placeholder="Filter games by name, player or opening…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 320 }}
        />
        <button onClick={() => setShowToc((v) => !v)}>
          contents {book.toc.length ? `(${book.toc.length})` : ""}
        </button>
        <button onClick={() => setShowIssues((v) => !v)}>
          issues ({issues.length})
        </button>
        <div style={{ marginLeft: "auto" }} className="rowflex">
          <a href={api.exportPgnUrl(bookId)} download>
            <button>Export PGN</button>
          </a>
          <a href={api.exportJsonUrl(bookId)} download>
            <button>Export JSON</button>
          </a>
        </div>
      </div>

      {showToc && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <header>Contents</header>
          <div className="body">
            {book.toc.length === 0 && <div className="tiny">No table of contents detected.</div>}
            {book.toc.map((t, i) => (
              <div key={i} style={{ paddingLeft: t.level * 14 }} className="row" onClick={() => t.page && setShowToc(false)}>
                <div className="grow title">{t.title}</div>
                <span className="sub">{t.page ?? ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {showIssues && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <header>
            Extraction issues ({issues.length})
            <div className="right">
              <span className="tiny">nothing here is hidden: every problem is listed</span>
            </div>
          </header>
          <div className="body" style={{ maxHeight: 280 }}>
            {issues.length === 0 && <div className="tiny">No issues found.</div>}
            {issues.map((i) => (
              <div className="issue" key={i.id}>
                <span className={`badge ${i.severity === "error" ? "bad" : i.severity === "warning" ? "warn" : ""}`}>
                  {i.severity}
                </span>
                <div>
                  <div className="msg">
                    {i.game_id && (
                      <a
                        href="#"
                        onClick={(e) => {
                          e.preventDefault();
                          nav(`/book/${bookId}/game/${i.game_id}`);
                        }}
                      >
                        game
                      </a>
                    )}{" "}
                    {i.message}
                  </div>
                  <div className="where">
                    <span className="code">{i.code}</span>
                    {i.page ? ` · page ${i.page}` : ""}
                    {i.source_text ? <span className="raw"> · {i.source_text}</span> : null}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="panel">
        <header>Games ({filtered.length})</header>
        <div className="body" style={{ maxHeight: "none" }}>
          {filtered.length === 0 && <div className="empty">No games matched.</div>}
          {filtered.map((g) => (
            <div
              key={g.id}
              className="row"
              onClick={() => nav(`/book/${bookId}/game/${g.id}`)}
            >
              <span className={`badge ${g.parse_status === "ok" ? "ok" : g.parse_status === "partial" ? "warn" : "bad"}`}>
                {g.parse_status}
              </span>
              <div className="grow">
                <div className="title">{g.title}</div>
                <div className="sub">
                  {g.players.white ?? "?"} – {g.players.black ?? "?"}
                  {g.result ? ` · ${g.result}` : ""}
                  {g.start_page ? ` · p. ${g.start_page}` : ""}
                  {g.chapter_id ? ` · ${chapterTitle(g.chapter_id) ?? ""}` : ""}
                </div>
              </div>
              <span className="sub">{g.ply_count} plies</span>
              {g.variation_count > 0 && <span className="badge accent">{g.variation_count} var</span>}
              <span className="tiny">{g.confidence.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </div>

      {book.chapters.length > 0 && (
        <>
          <h3 style={{ margin: "22px 0 10px", fontSize: 13, letterSpacing: "0.08em", color: "var(--text-3)", textTransform: "uppercase" }}>
            Structure
          </h3>
          <div className="panel">
            <div className="body">
              {book.chapters.map((c) => (
                <div key={c.id} style={{ paddingLeft: c.level * 14 }} className="row">
                  <span className="badge">{c.kind}</span>
                  <div className="grow title">{c.title}</div>
                  <span className="sub">
                    {c.game_ids.length} games · p. {c.start_page}–{c.end_page ?? "?"} ·
                    confidence {c.confidence.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Metric({ n, label, tone }: { n: number; label: string; tone?: "ok" | "warn" | "bad" }) {
  const color = tone === "bad" ? "var(--bad)" : tone === "warn" ? "var(--warn)" : tone === "ok" ? "var(--good)" : "var(--paper)";
  return (
    <div className="card" style={{ padding: "14px 16px" }}>
      <b style={{ fontFamily: "var(--serif)", fontSize: 24, color, lineHeight: 1 }}>{n.toLocaleString()}</b>
      <div className="tiny" style={{ textTransform: "uppercase", letterSpacing: "0.08em", marginTop: 4 }}>
        {label}
      </div>
    </div>
  );
}