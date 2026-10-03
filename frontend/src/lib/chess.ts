/** Chess helpers for the reader: FEN parsing, piece rendering, variation trees. */

export type Side = "w" | "b";

export const FILES = ["a", "b", "c", "d", "e", "f", "g", "h"] as const;
export const RANKS = ["8", "7", "6", "5", "4", "3", "2", "1"] as const;

export interface ParsedFen {
  board: (string | null)[]; // 64 entries, index 0 = a8
  turn: Side;
  castling: string;
  ep: string | null;
}

export function parseFen(fen: string): ParsedFen {
  const [placement, turn, castling, ep] = fen.split(" ");
  const board: (string | null)[] = new Array(64).fill(null);
  let idx = 0;
  for (const ch of placement) {
    if (ch === "/") continue;
    if (/[1-8]/.test(ch)) {
      idx += Number(ch);
      continue;
    }
    board[idx++] = ch;
  }
  return { board, turn: turn === "b" ? "b" : "w", castling, ep: ep === "-" ? null : ep };
}

export function squareIndex(file: number, rank: number): number {
  return rank * 8 + file;
}

export function indexToSquare(index: number): string {
  return FILES[index % 8] + RANKS[Math.floor(index / 8)];
}

/** Unicode piece glyphs: white first so colour is obvious at a glance. */
export const PIECE_GLYPH: Record<string, string> = {
  K: "♔", Q: "♕", R: "♖", B: "♗", N: "♘", P: "♙",
  k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟",
};

/** Pseudo-legal destinations, used only for the "where could I go" hint. */
export function legalDestinations(fen: string): Set<string> {
  const { board, turn, castling, ep } = parseFen(fen);
  const out = new Set<string>();
  const enemy = turn === "w" ? "b" : "w";
  const at = (f: number, r: number): string | null => {
    if (f < 0 || f > 7 || r < 0 || r > 7) return null;
    return board[squareIndex(f, r)];
  };
  const push = (f: number, r: number) => {
    const piece = at(f, r);
    if (!piece || piece === enemy) out.add(indexToSquare(squareIndex(f, r)));
  };

  for (let r = 0; r < 8; r++) {
    for (let f = 0; f < 8; f++) {
      const piece = at(f, r);
      if (!piece) continue;
      if (piece === (turn === "w" ? "p" : "P")) continue;
      const kind = piece.toUpperCase();
      const sign = piece === kind ? 1 : -1;
      if (kind === "N") {
        for (const [df, dr] of [[1, 2], [2, 1], [2, -1], [1, -2], [-1, -2], [-2, -1], [-2, 1], [-1, 2]]) {
          push(f + df, r + sign * dr);
        }
      } else if (kind === "K") {
        for (let df = -1; df <= 1; df++) for (let dr = -1; dr <= 1; dr++) if (df || dr) push(f + df, r + dr);
      } else {
        const dirs =
          kind === "B" ? [[1, 1], [1, -1], [-1, 1], [-1, -1]]
          : kind === "R" ? [[1, 0], [-1, 0], [0, 1], [0, -1]]
          : [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];
        for (const [df, dr] of dirs) {
          let nf = f + df;
          let nr = r + dr;
          while (nf >= 0 && nf < 8 && nr >= 0 && nr < 8) {
            const occupant = at(nf, nr);
            if (occupant === enemy) {
              push(nf, nr);
              break;
            }
            if (occupant) break;
            push(nf, nr);
            nf += df;
            nr += dr;
          }
        }
      }
    }
  }
  // pawns
  for (let r = 0; r < 8; r++) {
    for (let f = 0; f < 8; f++) {
      const piece = at(f, r);
      if (!piece) continue;
      const mine = turn === "w" ? "P" : "p";
      const theirs = turn === "w" ? "p" : "P";
      if (piece !== mine) continue;
      const step = turn === "w" ? -1 : 1;
      const startRank = turn === "w" ? 6 : 1;
      if (!at(f, r + step)) push(f, r + step);
      if (r === startRank && !at(f, r + 2 * step) && !at(f, r + step)) push(f, r + 2 * step);
      for (const df of [-1, 1]) {
        if (at(f + df, r + step) === theirs) push(f + df, r + step);
        if (ep && ep === indexToSquare(squareIndex(f + df, r + step))) push(f + df, r + step);
      }
    }
  }
  // castling
  const rights = castling;
  if (rights !== "-") {
    if (turn === "w" && rights.includes("K") && !at(5, 0) && !at(6, 0) && at(7, 0) === "R" && at(4, 0) === "K") {
      out.add("g1");
    }
    if (turn === "w" && rights.includes("Q") && !at(3, 0) && !at(2, 0) && !at(1, 0) && at(0, 0) === "R") {
      out.add("c1");
    }
    if (turn === "b" && rights.includes("k") && !at(5, 7) && !at(6, 7) && at(7, 7) === "r") {
      out.add("g8");
    }
    if (turn === "b" && rights.includes("q") && !at(3, 7) && !at(2, 7) && !at(1, 7) && at(0, 7) === "r") {
      out.add("c8");
    }
  }
  return out;
}

/** Is the king of `side` attacked?  (for the check highlight) */
export function isChecked(fen: string): boolean {
  const { board, turn } = parseFen(fen);
  const kingChar = turn === "w" ? "K" : "k";
  let kingSquare = -1;
  for (let i = 0; i < 64; i++) if (board[i] === kingChar) kingSquare = i;
  if (kingSquare < 0) return false;
  const attacked = legalDestinationsFor(board, turn === "w" ? "b" : "w");
  return attacked.has(indexToSquare(kingSquare));
}

function legalDestinationsFor(board: (string | null)[], side: Side): Set<string> {
  const enemy = side === "w" ? "b" : "w";
  const out = new Set<string>();
  const at = (f: number, r: number) => {
    if (f < 0 || f > 7 || r < 0 || r > 7) return null;
    return board[squareIndex(f, r)];
  };
  for (let r = 0; r < 8; r++) {
    for (let f = 0; f < 8; f++) {
      const piece = at(f, r);
      if (!piece) continue;
      if ((piece === piece.toUpperCase() ? "w" : "b") !== side) continue;
      const kind = piece.toUpperCase();
      const push = (nf: number, nr: number) => {
        const occupant = at(nf, nr);
        if (!occupant || (occupant === occupant.toUpperCase() ? "w" : "b") === enemy) {
          out.add(indexToSquare(squareIndex(nf, nr)));
        }
      };
      if (kind === "N" || kind === "K") {
        const dirs = kind === "K"
          ? [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]
          : [[1, 2], [2, 1], [2, -1], [1, -2], [-1, -2], [-2, -1], [-2, 1], [-1, 2]];
        for (const [df, dr] of dirs) push(f + df, r + dr);
      } else {
        const dirs = kind === "B" ? [[1, 1], [1, -1], [-1, 1], [-1, -1]]
          : kind === "R" ? [[1, 0], [-1, 0], [0, 1], [0, -1]]
          : [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];
        for (const [df, dr] of dirs) {
          let nf = f + df;
          let nr = r + dr;
          while (nf >= 0 && nf < 8 && nr >= 0 && nr < 8) {
            const occupant = at(nf, nr);
            if (occupant) {
              if ((occupant === occupant.toUpperCase() ? "w" : "b") !== side) {
                out.add(indexToSquare(squareIndex(nf, nr)));
              }
              break;
            }
            out.add(indexToSquare(squareIndex(nf, nr)));
            nf += df;
            nr += dr;
          }
        }
      }
    }
  }
  return out;
}