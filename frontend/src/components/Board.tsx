import { useMemo } from "react";
import {
  FILES,
  RANKS,
  PIECE_GLYPH,
  indexToSquare,
  isChecked,
  legalDestinations,
  parseFen,
} from "@/lib/chess";

interface BoardProps {
  fen: string;
  orientation?: "white" | "black";
  lastMove?: [string, string] | null;
  onSquareClick?: (square: string) => void;
  selected?: string | null;
  arrows?: [string, string][];
}

/** The board is the visual anchor of the product, so it gets real attention:
 *  crisp coordinates, a warm palette that reads as paper and ink, subtle
 *  highlights, and an optional move arrow. */
export function Board({
  fen,
  orientation = "white",
  lastMove = null,
  onSquareClick,
  selected = null,
  arrows = [],
}: BoardProps) {
  const { board } = useMemo(() => parseFen(fen), [fen]);
  const hints = useMemo(() => (selected ? legalDestinations(fen) : new Set<string>()), [fen, selected]);
  const checked = useMemo(() => isChecked(fen), [fen]);

  const files = orientation === "white" ? FILES : [...FILES].reverse();
  const ranks = orientation === "white" ? RANKS : [...RANKS].reverse();

  const kingSquare = useMemo(() => {
    const c = board.indexOf(orientation === "white" ? "K" : "k");
    return c >= 0 ? indexToSquare(c) : null;
  }, [board, orientation]);

  const lastSquares = lastMove ? new Set(lastMove) : new Set<string>();

  return (
    <div className="board-wrap">
      <svg
        className="board"
        viewBox="0 0 800 800"
        role="img"
        aria-label={`Chess position, ${orientation} at the bottom`}
      >
        <defs>
          <filter id="pieceShadow" x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="2.5" stdDeviation="2.4" floodColor="#000" floodOpacity="0.45" />
          </filter>
          <radialGradient id="checkGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="var(--hl-check)" />
            <stop offset="100%" stopColor="rgba(209,106,90,0)" />
          </radialGradient>
          <marker id="arrowhead" markerWidth="5" markerHeight="5" refX="3" refY="1.5" orient="auto">
            <path d="M0,0 L4,1.5 L0,3 z" fill="#c9a227" />
          </marker>
        </defs>

        {files.map((file) =>
          ranks.map((rank) => {
            const fileIdx = FILES.indexOf(file);
            const rankIdx = RANKS.indexOf(rank);
            const index = rankIdx * 8 + fileIdx;
            const col = files.indexOf(file);
            const row = ranks.indexOf(rank);
            const x = col * 100;
            const y = row * 100;
            const piece = board[index];
            const square = file + rank;
            const isLight = (fileIdx + rankIdx) % 2 === 0;
            const isLast = lastSquares.has(square);
            const isCheck = checked && square === kingSquare;
            const isHint = hints.has(square);
            const isSel = selected === square;

            return (
              <g
                key={square}
                onClick={() => onSquareClick?.(square)}
                style={{ cursor: onSquareClick ? "pointer" : "default" }}
              >
                <rect
                  x={x}
                  y={y}
                  width={100}
                  height={100}
                  fill={isLight ? "var(--light)" : "var(--dark)"}
                />
                {isLast && (
                  <rect
                    x={x}
                    y={y}
                    width={100}
                    height={100}
                    fill="var(--hl-last)"
                  />
                )}
                {isCheck && (
                  <rect x={x} y={y} width={100} height={100} fill="url(#checkGlow)" />
                )}
                {isSel && (
                  <rect
                    x={x + 4}
                    y={y + 4}
                    width={92}
                    height={92}
                    fill="none"
                    stroke="#c9a227"
                    strokeWidth={4}
                    opacity={0.85}
                  />
                )}
                {isHint && (
                  <circle
                    cx={x + 50}
                    cy={y + 50}
                    r={piece ? 13 : 17}
                    fill={piece ? "#c9a227" : "rgba(20,18,15,0.28)"}
                    opacity={0.55}
                  />
                )}
                {piece && (
                  <text
                    x={x + 50}
                    y={y + 68}
                    textAnchor="middle"
                    fontSize={68}
                    fill={piece === piece.toUpperCase() ? "#fbf7ee" : "#1a1714"}
                    stroke={piece === piece.toUpperCase() ? "#1a1714" : "#fbf7ee"}
                    strokeWidth={1.4}
                    paintOrder="stroke"
                    style={{ filter: "url(#pieceShadow)", userSelect: "none" }}
                  >
                    {PIECE_GLYPH[piece]}
                  </text>
                )}
                {/* coordinates: files bottom, ranks left, like a real board */}
                {row === ranks.length - 1 && (
                  <text
                    x={x + 94}
                    y={y + 94}
                    textAnchor="end"
                    fontSize={15}
                    fill={isLight ? "var(--dark)" : "var(--light)"}
                    opacity={0.75}
                    style={{ userSelect: "none" }}
                  >
                    {file}
                  </text>
                )}
                {col === 0 && (
                  <text
                    x={x + 7}
                    y={y + 20}
                    fontSize={15}
                    fill={isLight ? "var(--dark)" : "var(--light)"}
                    opacity={0.75}
                    style={{ userSelect: "none" }}
                  >
                    {rank}
                  </text>
                )}
              </g>
            );
          }),
        )}

        {arrows.map(([from, to], i) => {
          const a = squareCentre(from, files, ranks);
          const b = squareCentre(to, files, ranks);
          return (
            <line
              key={`${from}-${to}-${i}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke="#c9a227"
              strokeWidth={9}
              strokeLinecap="round"
              opacity={0.75}
              markerEnd="url(#arrowhead)"
            />
          );
        })}
      </svg>
    </div>
  );
}

function squareCentre(
  square: string,
  files: readonly string[],
  ranks: readonly string[],
): { x: number; y: number } {
  const file = square[0];
  const rank = square[1];
  const col = files.indexOf(file);
  const row = ranks.indexOf(rank);
  return { x: col * 100 + 50, y: row * 100 + 50 };
}