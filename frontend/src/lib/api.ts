/** Typed API client.  Everything is local; there is no base URL to configure
 *  beyond the dev proxy, and no credential is ever sent. */

const BASE = "";

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${path}: ${detail.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export interface Metrics {
  pages: number;
  chapters: number;
  sections: number;
  games: number;
  moves: number;
  variations: number;
  comments: number;
  diagrams: number;
  board_diagrams: number;
  nodes: number;
  words: number;
  issues: number;
  errors: number;
  warnings: number;
  ocr_used: boolean;
  ocr_pages: number;
  recovery_level: number;
}

export interface TocEntry {
  level: number;
  title: string;
  page: number | null;
  target_id: string | null;
}

export interface BookSummary {
  id: string;
  title: string;
  source_filename: string;
  page_count: number;
  status: string;
  has_text_layer: boolean;
  created_at: number;
  updated_at: number;
  metrics: Metrics;
}

export interface BookDetail extends BookSummary {
  chapters: Chapter[];
  diagrams: DiagramRow[];
  issues_summary: { errors: number; warnings: number };
  progress: Progress | null;
  toc: TocEntry[];
  extraction_report: Record<string, unknown>;
}

export interface Chapter {
  id: string;
  book_id: string;
  parent_id: string | null;
  idx: number;
  level: number;
  kind: string;
  title: string;
  start_page: number | null;
  end_page: number | null;
  game_ids: string[];
  confidence: number;
}

export interface GameSummary {
  id: string;
  book_id: string;
  chapter_id: string | null;
  idx: number;
  title: string;
  players: { white?: string | null; black?: string | null };
  event: string | null;
  site: string | null;
  date: string | null;
  round: string | null;
  result: string | null;
  eco: string | null;
  opening: string | null;
  ply_count: number;
  node_count: number;
  variation_count: number;
  comment_count: number;
  start_page: number | null;
  end_page: number | null;
  root_fen: string;
  final_fen: string;
  confidence: number;
  parse_status: "ok" | "partial" | "failed";
  detection_heuristic: string;
  stats: Record<string, number>;
}

export interface NodeRow {
  id: string;
  game_id: string;
  parent_id: string | null;
  ply: number;
  move_number: number;
  side: "w" | "b";
  san: string | null;
  move_uci: string | null;
  fen_before: string;
  fen_after: string;
  annotations: string[];
  nags: string[];
  comment_ids: string[];
  child_ids: string[];
  mainline_child_id: string | null;
  is_mainline: boolean;
  variation_index: number;
  variation_depth: number;
  started_by: string;
  move_origin: string;
  source_text: string;
  page: number | null;
  bbox: number[] | null;
  confidence: number;
  depth: number;
}

export interface CommentRow {
  id: string;
  game_id: string | null;
  node_id: string | null;
  page: number | null;
  bbox: number[] | null;
  text: string;
  confidence: number;
  anchor_kind: string;
  signals: Record<string, number>;
}

export interface TreePayload {
  nodes: NodeRow[];
  comments: Record<string, CommentRow[]>;
}

export interface IssueRow {
  id: string;
  game_id: string | null;
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  page: number | null;
  node_id: string | null;
  source_text: string | null;
  context: Record<string, unknown>;
}

export interface DiagramRow {
  id: string;
  book_id: string;
  game_id: string | null;
  node_id: string | null;
  page: number;
  bbox: number[];
  width: number;
  height: number;
  kind: string;
  is_board_candidate: boolean;
  board_score: number;
  caption: string | null;
  fen: string | null;
  position_confidence: string;
  position_method: string | null;
  grid: Record<string, number>;
  confidence: number;
  notes: string[];
}

export interface Progress {
  book_id: string;
  scope: string;
  game_id: string | null;
  node_id: string | null;
  chapter_id: string | null;
  move_index: number;
  orientation: "white" | "black";
}

export interface JobStage {
  key: string;
  label: string;
  status: "pending" | "running" | "done" | "skipped" | "failed";
  detail: string;
  progress: number;
}

export interface ImportJob {
  id: string;
  book_id: string | null;
  filename: string;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  stages: JobStage[];
  messages: string[];
  logs: string[];
  error: string | null;
  reuse_existing_book_id: string | null;
}

export interface SearchHit {
  kind: string;
  ref_id: string;
  book_id: string;
  title: string;
  page: number | null;
  players: string | null;
  snip: string;
  score: number;
}

export const api = {
  health: () => json<Record<string, unknown>>("/api/health"),
  books: () => json<{ books: BookSummary[] }>("/api/books"),
  book: (id: string) => json<BookDetail>(`/api/books/${id}`),
  deleteBook: (id: string) => json<{ deleted: string }>(`/api/books/${id}`, { method: "DELETE" }),
  chapters: (id: string) => json<{ chapters: Chapter[] }>(`/api/books/${id}/chapters`),
  games: (id: string, chapter?: string) =>
    json<{ games: GameSummary[] }>(
      `/api/books/${id}/games${chapter ? `?chapter_id=${chapter}` : ""}`,
    ),
  tree: (gameId: string) => json<TreePayload>(`/api/games/${gameId}/tree`),
  issues: (bookId: string) => json<{ issues: IssueRow[] }>(`/api/books/${bookId}/issues`),
  job: (id: string) => json<ImportJob>(`/api/import/${id}`),
  search: (q: string, bookId?: string) =>
    json<{ results: SearchHit[] }>(
      `/api/search?q=${encodeURIComponent(q)}${bookId ? `&book_id=${bookId}` : ""}`,
    ),
  saveProgress: (bookId: string, payload: Partial<Progress>) =>
    json<{ saved: boolean }>(`/api/books/${bookId}/progress`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  addCorrection: (bookId: string, payload: Record<string, unknown>) =>
    json<{ id: string }>(`/api/books/${bookId}/corrections`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  sourcePdfUrl: (bookId: string, page: number) =>
    `${BASE}/api/source/${bookId}/pdf?page=${page}#page=${page}`,
  exportPgnUrl: (bookId: string) => `${BASE}/api/books/${bookId}/export.pgn`,
  exportJsonUrl: (bookId: string) => `${BASE}/api/books/${bookId}/export.json`,

  async upload(file: File): Promise<ImportJob> {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/books/import", { method: "POST", body: form });
    if (!res.ok) throw new Error(`upload failed: ${res.status}`);
    return (await res.json()) as ImportJob;
  },
};