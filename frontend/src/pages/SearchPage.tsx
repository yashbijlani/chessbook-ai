import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type SearchHit } from "@/lib/api";

export function SearchPage() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!q.trim()) {
      setHits(null);
      return;
    }
    setBusy(true);
    const t = window.setTimeout(() => {
      api
        .search(q)
        .then((r) => setHits(r.results))
        .catch(() => setHits([]))
        .finally(() => setBusy(false));
    }, 220);
    return () => window.clearTimeout(t);
  }, [q]);

  const grouped = useMemo(() => {
    const map = new Map<string, SearchHit[]>();
    for (const h of hits ?? []) {
      const book = h.book_id;
      map.set(book, [...(map.get(book) ?? []), h]);
    }
    return [...map.entries()];
  }, [hits]);

  return (
    <div className="page narrow">
      <div className="page-head">
        <h1>Search</h1>
        <div className="sub">Local full-text search across books, commentary and player names.</div>
      </div>

      <input
        autoFocus
        placeholder="e.g. exchange sacrifice, Kasparov, 14.Bg3"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ width: "100%", fontSize: 17, padding: "12px 14px" }}
      />

      {busy && <p className="tiny" style={{ marginTop: 10 }}>searching…</p>}

      {!q.trim() && (
        <div className="empty">
          Try a phrase from the book. Results link straight into the reader.
        </div>
      )}

      {q.trim() && hits?.length === 0 && !busy && (
        <div className="empty">Nothing found for “{q}”.</div>
      )}

      {grouped.map(([bookId, list]) => (
        <div className="panel" key={bookId} style={{ marginTop: 16 }}>
          <header>{list.length} result{list.length === 1 ? "" : "s"}</header>
          <div className="body" style={{ maxHeight: "none" }}>
            {list.map((h, i) => (
              <div
                key={`${h.ref_id}-${i}`}
                className="row"
                onClick={() => nav(`/book/${bookId}/game/${h.ref_id}`)}
              >
                <span className="badge">{h.kind}</span>
                <div className="grow">
                  <div className="title">{h.title || h.ref_id}</div>
                  <div className="sub">{h.snip?.replace(/\s+/g, " ")}</div>
                </div>
                {h.page ? <span className="sub">p. {h.page}</span> : null}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}