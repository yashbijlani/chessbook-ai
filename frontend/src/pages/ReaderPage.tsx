import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Board } from "@/components/Board";
import { api, type BookDetail, type GameSummary, type IssueRow, type NodeRow, type TreePayload } from "@/lib/api";
import { buildModel, flattenPath, useNavigation, variationLine } from "@/lib/tree";
import { indexToSquare, legalDestinations, parseFen } from "@/lib/chess";

export function ReaderPage() {
  const { bookId = "", gameId = "" } = useParams();
  const nav = useNavigate();
  const [params] = useSearchParams();

  const [book, setBook] = useState<BookDetail | null>(null);
  const [game, setGame] = useState<GameSummary | null>(null);
  const [tree, setTree] = useState<TreePayload | null>(null);
  const [issues, setIssues] = useState<IssueRow[]>([]);
  const [orientation, setOrientation] = useState<"white" | "black">("white");
  const [selected, setSelected] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);
  const [showDebug, setShowDebug] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const saveTimer = useRef<number | null>(null);

  const model = useMemo(() => buildModel(tree), [tree]);
  const navState = useNavigation(model, params.get("node"));

  useEffect(() => {
    let alive = true;
    api
      .book(bookId)
      .then((b) => {
        if (!alive) return;
        setBook(b);
        setOrientation((b.progress?.orientation as "white" | "black") ?? "white");
      })
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [bookId]);

  useEffect(() => {
    let alive = true;
    Promise.all([api.games(bookId), api.issues(bookId)])
      .then(([g, i]) => {
        if (!alive) return;
        setIssues(i.issues);
        const found = g.games.find((x) => x.id === gameId) ?? g.games[0];
        if (!found) return;
        setGame(found);
        if (found.id !== gameId) nav(`/book/${bookId}/game/${found.id}`, { replace: true });
      })
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [bookId, gameId, nav]);

  useEffect(() => {
    if (!game) return;
    let alive = true;
    setTree(null);
    api
      .tree(game.id)
      .then((t) => alive && setTree(t))
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [game]);

  // resume where the reader left off
  const resumed = useRef<string | null>(null);
  useEffect(() => {
    if (!tree || !game || !book?.progress) return;
    if (book.progress.game_id !== game.id) return;
    if (params.get("node") || resumed.current === game.id) return;
    if (book.progress.node_id && model.byId.has(book.progress.node_id)) {
      resumed.current = game.id;
      navState.goTo(book.progress.node_id);
    }
  }, [tree, game, book, model, navState, params]);

  // persist reading position (debounced)
  useEffect(() => {
    if (!game || !navState.currentId) return;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void api.saveProgress(bookId, {
        game_id: game.id,
        node_id: navState.currentId,
        move_index: navState.path.length - 1,
        orientation,
      });
    }, 400);
    return () => {
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    };
  }, [game, navState.currentId, navState.path.length, orientation, bookId]);

  // keyboard shortcuts (charter #21)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
      switch (e.key) {
        case "ArrowRight":
          navState.next();
          break;
        case "ArrowLeft":
          navState.previous();
          break;
        case "ArrowUp":
          navState.prevSiblingVariation();
          break;
        case "ArrowDown":
          navState.nextSiblingVariation();
          break;
        case "Home":
          navState.first();
          break;
        case "End":
          navState.last();
          break;
        case " ":
          e.preventDefault();
          navState.exitVariation();
          break;
        case "f":
        case "F":
          setOrientation((o) => (o === "white" ? "black" : "white"));
          break;
        case "Escape":
          setShowSource(false);
          break;
        default:
          return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navState]);

  const current = model.byId.get(navState.currentId);
  const moveList = useMemo(() => flattenPath(model, navState.path), [model, navState.path]);
  const comments = model.commentsOf(navState.currentId);
  const variations = model.variationsOf.get(navState.currentId) ?? [];

  const lastMove = useMemo(() => lastMoveOf(model, navState.path), [model, navState.path]);
  const arrows = useMemo<[string, string][]>(() => {
    const uci = current?.move_uci;
    return uci ? [[uci.slice(0, 2), uci.slice(2, 4)]] : [];
  }, [current]);

  const clickSquare = useCallback(
    (square: string) => {
      if (!current) return;
      if (!selected) {
        setSelected(square);
        return;
      }
      // find a child of the current node that matches the pair
      const from = selected;
      const kids = model.childrenOf.get(current.id) ?? [];
      const match = kids
        .map((id) => model.byId.get(id)?.move_uci)
        .find((uci) => uci && uci[0] === from[0] && uci[1] === from[1] && uci.slice(2) === square);
      if (match) {
        const child = kids.find((id) => model.byId.get(id)?.move_uci === match);
        if (child) navState.goTo(child);
      }
      setSelected(null);
    },
    [current, selected, model, navState],
  );

  if (error) return <div className="page"><div className="empty">Could not load: {error}</div></div>;
  if (!book || !game) return <div className="page"><div className="empty">Loading…</div></div>;

  return (
    <div className="reader">
      <div className="board-col">
        <Board
          fen={current?.fen_before ?? game.root_fen}
          orientation={orientation}
          lastMove={lastMove}
          arrows={arrows}
          selected={selected}
          onSquareClick={clickSquare}
        />

        <div className="controls">
          <button onClick={navState.first} title="Beginning (Home)">⏮</button>
          <button onClick={navState.previous} title="Previous (←)">◀</button>
          <span className="move-label">
            {current ? (
              current.san ? (
                <>
                  {current.side === "w" ? `${current.move_number}.` : `${current.move_number}...`}
                  <b> {current.san}</b>
                </>
              ) : (
                <b>start</b>
              )
            ) : null}
          </span>
          <button onClick={navState.next} title="Next (→)">▶</button>
          <button onClick={navState.last} title="End (End)">⏭</button>
          <button
            onClick={() => setOrientation((o) => (o === "white" ? "black" : "white"))}
            title="Flip board (F)"
          >
            ⇅
          </button>
          {navState.inVariation && (
            <button onClick={navState.exitVariation} title="Back to the main line (Space)">
              main line
            </button>
          )}
          <div className="spacer" />
          <button
            className={showSource ? "primary" : "ghost"}
            onClick={() => setShowSource((v) => !v)}
            title="Open the original PDF at this page"
          >
            source
          </button>
          <button
            className={showDebug ? "primary" : "ghost"}
            onClick={() => setShowDebug((v) => !v)}
            title="Extraction inspector"
          >
            inspect
          </button>
        </div>

        <MoveStrip
          items={moveList}
          onPick={(id) => navState.goTo(id)}
        />

        {selected && <SquareInspector fen={current?.fen_before ?? game.root_fen} square={selected} />}
      </div>

      <div className="text-col">
        <header className="book-head">
          <div className="crumb">
            <a href="#" onClick={(e) => { e.preventDefault(); nav("/library"); }}>
              {book.title}
            </a>
            {game.chapter_id ? " · chapter" : ""}
            {game.start_page ? ` · p. ${game.start_page}` : ""}
          </div>
          <h2>{game.title || `Game ${game.idx + 1}`}</h2>
          <div className="players">
            {game.players.white ?? "?"} <span style={{ color: "var(--text-3)" }}>vs</span>{" "}
            {game.players.black ?? "?"}
            {game.result ? ` · ${game.result}` : ""}
            {game.event ? ` · ${game.event}` : ""}
            {game.date ? ` · ${game.date}` : ""}
            {game.eco ? ` · ${game.eco}` : ""}
          </div>
          <div className="rowflex" style={{ marginTop: 10 }}>
            <span className={`badge ${statusTone(game.parse_status)}`}>{game.parse_status}</span>
            <span className="badge">confidence {game.confidence.toFixed(2)}</span>
            {game.variation_count > 0 && (
              <span className="badge accent">{game.variation_count} variations</span>
            )}
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <a className="tiny" href={`/api/games/${game.id}/export.pgn`} download>
                PGN
              </a>
            </div>
          </div>
        </header>

        {comments.length === 0 && !variations.length && (
          <div className="empty">
            No commentary was attached to this position.
            {game.comment_count > 0 && " (Some commentary could not be placed with confidence.)"}
          </div>
        )}

        {comments.map((c) => (
          <div className="commentary" key={c.id}>
            <div className="head">
              <span className="who">{current?.san ?? "position"}</span>
              <span className="badge">{c.anchor_kind.replace("_", " ")}</span>
              <span className="conf" title="association confidence">
                {Math.round(c.confidence * 100)}%
              </span>
            </div>
            <div className="prose">
              <CommentText text={c.text} onJump={(n, s) => jumpTo(model, navState, n, s)} />
            </div>
            {c.page && (
              <button
                className="ghost tiny"
                style={{ margin: "6px 0 2px" }}
                onClick={() => setShowSource(true)}
              >
                p. {c.page} in the PDF
              </button>
            )}
          </div>
        ))}

        {variations.map((vid) => {
          const line = variationLine(model, vid);
          return (
            <div className="variation" key={vid}>
              <div className="vhead">
                <b>
                  {line[0] ? `${line[0].move_number}${line[0].side === "b" ? "..." : "."}${line[0].san}` : "variation"}
                </b>
                <span>{model.byId.get(vid)?.started_by.replace("_", " ")}</span>
                <button
                  className="ghost tiny"
                  style={{ marginLeft: "auto" }}
                  onClick={() => navState.enterVariation(vid)}
                >
                  enter
                </button>
              </div>
              <div className="moves">
                {line.map((n) => (
                  <span
                    key={n.id}
                    className={`tok ${n.id === navState.currentId ? "current" : ""} ${
                      n.nags?.length ? "nag" : ""
                    }`}
                    onClick={() => navState.enterVariation(n.id)}
                  >
                    {n.side === "w" ? `${n.move_number}. ` : n.parent_id === model.rootId ? `${n.move_number}...` : ""}
                    {n.san}
                    {n.nags?.length ? nagSymbol(n.nags[0]) : ""}
                  </span>
                ))}
              </div>
            </div>
          );
        })}

        {showSource && current?.page && (
          <div className="panel">
            <header>
              Original page {current.page}
              <div className="right">
                <a className="tiny" href={api.sourcePdfUrl(bookId, current.page)} target="_blank" rel="noreferrer">
                  open
                </a>
                <button className="ghost tiny" onClick={() => setShowSource(false)}>
                  close
                </button>
              </div>
            </header>
            <div className="body" style={{ padding: 0 }}>
              <iframe
                className="pdf-frame"
                title={`Source page ${current.page}`}
                src={api.sourcePdfUrl(bookId, current.page)}
              />
              <p className="pdf-note">
                The PDF is the ground truth. If a move or comment looks wrong, compare it with the
                original here and record a correction in the inspector.
              </p>
            </div>
          </div>
        )}

        {showDebug && <Inspector game={game} node={current} issues={issues} />}
      </div>
    </div>
  );
}

function MoveStrip({
  items,
  onPick,
}: {
  items: ReturnType<typeof flattenPath>;
  onPick: (id: string) => void;
}) {
  return (
    <div className="movelist">
      {items.length === 0 && <span className="tiny">no moves reconstructed</span>}
      {items.map((m, i) => (
        <span key={m.nodeId}>
          {m.isVariationRoot && <span className="var-branch" />}
          {m.side === "w" && (
            <span className="no" style={{ marginLeft: i === 0 ? 0 : 8 }}>
              {m.number}.
            </span>
          )}
          <button
            className={`${m.isCurrent ? "current" : m.visited ? "visited" : ""} ${m.nag ? "nag" : ""}`}
            onClick={() => onPick(m.nodeId)}
            title={m.hasComment ? "has commentary" : undefined}
          >
            {m.san}
            {m.nag}
          </button>
        </span>
      ))}
    </div>
  );
}

function SquareInspector({ fen, square }: { fen: string; square: string }) {
  const { board } = parseFen(fen);
  const idx = board.findIndex((_, i) => indexToSquare(i) === square);
  const piece = idx >= 0 ? board[idx] : null;
  const hints = legalDestinations(fen);
  return (
    <div className="panel">
      <header>
        {square} {piece ? `· ${piece}` : "· empty"}
        <div className="right">
          <button className="ghost tiny" onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))}>
            dismiss
          </button>
        </div>
      </header>
      <div className="body" style={{ paddingTop: 6 }}>
        <span className="tiny">Pseudo-legal destinations: {hints.has(square) ? "yes" : "no"}</span>
      </div>
    </div>
  );
}

function Inspector({ game, node, issues }: { game: GameSummary; node?: NodeRow; issues: IssueRow[] }) {
  const mine = issues.filter((i) => i.game_id === game.id);
  return (
    <div className="panel">
      <header>Extraction inspector</header>
      <div className="body">
        <dl className="kv">
          <dt>detected by</dt>
          <dd>{game.detection_heuristic || "—"}</dd>
          <dt>stats</dt>
          <dd>{JSON.stringify(game.stats)}</dd>
          <dt>node id</dt>
          <dd>{node?.id ?? "—"}</dd>
          <dt>started by</dt>
          <dd>{node?.started_by ?? "—"}</dd>
          <dt>confidence</dt>
          <dd>{node ? node.confidence.toFixed(3) : "—"}</dd>
          <dt>source text</dt>
          <dd>{node?.source_text || "—"}</dd>
          <dt>source bbox</dt>
          <dd>{node?.bbox ? node.bbox.map((v) => Math.round(v)).join(", ") : "—"}</dd>
          <dt>fen before</dt>
          <dd>{node?.fen_before ?? "—"}</dd>
          <dt>fen after</dt>
          <dd>{node?.fen_after ?? "—"}</dd>
        </dl>

        <h4 style={{ margin: "14px 0 4px", fontSize: 12, color: "var(--text-3)" }}>
          ISSUES FOR THIS GAME ({mine.length})
        </h4>
        {mine.length === 0 && <div className="tiny">None.</div>}
        {mine.slice(0, 40).map((i) => (
          <div className="issue" key={i.id}>
            <span className={`badge ${i.severity === "error" ? "bad" : i.severity === "warning" ? "warn" : ""}`}>
              {i.severity}
            </span>
            <div>
              <div className="msg">{i.message}</div>
              <div className="where">
                <span className="code">{i.code}</span>
                {i.page ? ` · page ${i.page}` : ""}
                {i.source_text ? <span className="raw"> · {i.source_text}</span> : null}
              </div>
              {Object.keys(i.context ?? {}).length > 0 && (
                <details>
                  <summary className="tiny">context</summary>
                  <pre className="mono tiny" style={{ whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(i.context, null, 2)}
                  </pre>
                </details>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Renders commentary, turning quoted moves into clickable jumps. */
function CommentText({
  text,
  onJump,
}: {
  text: string;
  onJump: (number: number, san: string) => void;
}) {
  const parts = useMemo(() => text.split(/(\b\d{1,3}\.{1,3}\s*[KQRBNP]?[a-h]?[x×]?[a-h][1-8](?:[+#=][QRBNPqrbnp]?)?\b)/g), [text]);
  return (
    <>
      {parts.map((p, i) =>
        /^\d{1,3}\./.test(p) ? (
          <span
            key={i}
            className="inline-move"
            onClick={() => {
              const m = p.match(/^(\d{1,3})\.{1,3}\s*(.*)$/);
              if (m) onJump(Number(m[1]), m[2]);
            }}
            title="Jump to this move"
          >
            {p}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

function lastMoveOf(model: ReturnType<typeof buildModel>, path: string[]): [string, string] | null {
  const id = path[path.length - 1];
  const uci = model.byId.get(id)?.move_uci;
  if (!uci) return null;
  return [uci.slice(0, 2), uci.slice(2, 4)];
}

function jumpTo(
  model: ReturnType<typeof buildModel>,
  navState: ReturnType<typeof useNavigation>,
  number: number,
  san: string,
) {
  for (const node of model.mainline) {
    const n = model.byId.get(node);
    if (!n || !n.san) continue;
    if (n.move_number !== number) continue;
    if (n.san.replace(/[+#]/g, "") === san.replace(/[+#]/g, "")) {
      navState.goTo(node);
      return;
    }
  }
}

function nagSymbol(nag: string): string {
  return (
    { $1: "!", $2: "!!", $3: "??", $4: "?!", $5: "!?", $6: "?!" }[nag] ?? ""
  );
}

function statusTone(status: string): string {
  return status === "ok" ? "ok" : status === "partial" ? "warn" : "bad";
}