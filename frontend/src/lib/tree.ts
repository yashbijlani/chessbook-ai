import { useCallback, useEffect, useMemo, useState } from "react";
import type { CommentRow, NodeRow, TreePayload } from "@/lib/api";

/**
 * The navigation model: a *path* of node ids from the root to the current
 * position, plus the set of ancestors that carry side lines.  This is what makes
 * "enter a variation and come back" work without flattening anything.
 */
export interface TreeModel {
  byId: Map<string, NodeRow>;
  rootId: string;
  mainline: string[];
  childrenOf: Map<string, string[]>;
  /** side lines hanging off a node, excluding the main-line child */
  variationsOf: Map<string, string[]>;
  commentsByNode: Map<string, CommentRow[]>;
  commentsOf: (nodeId: string | null) => CommentRow[];
}

export function buildModel(tree: TreePayload | null): TreeModel {
  const byId = new Map<string, NodeRow>();
  const childrenOf = new Map<string, string[]>();
  const variationsOf = new Map<string, string[]>();
  const commentsByNode = new Map<string, CommentRow[]>();

  for (const node of tree?.nodes ?? []) byId.set(node.id, node);
  for (const node of tree?.nodes ?? []) {
    if (!node.parent_id) continue;
    const kids = childrenOf.get(node.parent_id) ?? [];
    kids.push(node.id);
    childrenOf.set(node.parent_id, kids);
  }
  for (const [parent, kids] of childrenOf) {
    const main = byId.get(parent)?.mainline_child_id;
    const sides = kids.filter((k) => k !== main);
    if (sides.length) variationsOf.set(parent, sides);
  }
  for (const [nodeId, list] of Object.entries(tree?.comments ?? {})) {
    commentsByNode.set(nodeId, list);
  }

  let rootId = "";
  for (const node of tree?.nodes ?? []) {
    if (!node.parent_id) {
      rootId = node.id;
      break;
    }
  }

  const mainline: string[] = [];
  if (rootId) {
    let cur: string | undefined = rootId;
    const seen = new Set<string>();
    while (cur && !seen.has(cur)) {
      seen.add(cur);
      mainline.push(cur);
      cur = byId.get(cur)?.mainline_child_id ?? undefined;
    }
  }

  const commentsOf = (nodeId: string | null) =>
    nodeId ? (commentsByNode.get(nodeId) ?? []) : [];

  return { byId, rootId, mainline, childrenOf, variationsOf, commentsByNode, commentsOf };
}

export interface Navigation {
  path: string[];
  currentId: string;
  goTo: (nodeId: string) => void;
  next: () => void;
  previous: () => void;
  first: () => void;
  last: () => void;
  enterVariation: (nodeId: string) => void;
  exitVariation: () => void;
  inVariation: boolean;
  nextSiblingVariation: () => void;
  prevSiblingVariation: () => void;
}

export function useNavigation(model: TreeModel, initialId?: string | null): Navigation {
  const [path, setPath] = useState<string[]>(() => {
    const start = initialId && model.byId.has(initialId) ? initialId : model.rootId;
    return start ? pathTo(model, start) : [];
  });

  useEffect(() => {
    if (model.rootId && path.length === 0) setPath([model.rootId]);
  }, [model.rootId, path.length]);

  const goTo = useCallback(
    (nodeId: string) => {
      if (!model.byId.has(nodeId)) return;
      setPath(pathTo(model, nodeId));
    },
    [model],
  );

  const currentId = path[path.length - 1] ?? model.rootId;

  const next = useCallback(() => {
    const child = model.byId.get(currentId)?.mainline_child_id;
    if (child) setPath((p) => [...p, child]);
  }, [model, currentId]);

  const previous = useCallback(() => {
    setPath((p) => (p.length > 1 ? p.slice(0, -1) : p));
  }, []);

  const first = useCallback(() => setPath(model.rootId ? [model.rootId] : []), [model.rootId]);

  const last = useCallback(() => {
    if (model.mainline.length) setPath([model.mainline[model.mainline.length - 1]]);
  }, [model.mainline]);

  /** Walk into a side line: replace everything after the branch point. */
  const enterVariation = useCallback(
    (nodeId: string) => {
      if (!model.byId.has(nodeId)) return;
      const branch = pathTo(model, nodeId);
      const idx = path.findIndex((id) => id === nodeId);
      setPath(idx >= 0 ? [...path.slice(0, idx), ...branch.slice(branch.indexOf(nodeId))] : branch);
    },
    [model, path],
  );

  /** Leave a variation: jump to the main-line node at the same depth. */
  const exitVariation = useCallback(() => {
    if (!path.length) return;
    const head = model.byId.get(path[0]);
    if (head?.is_mainline) return;
    const depth = path.length - 1;
    const target = model.mainline[depth] ?? model.mainline[model.mainline.length - 1];
    if (target) setPath(pathTo(model, target));
  }, [model, path]);

  const inVariation = path.length > 0 && !model.byId.get(path[0])?.is_mainline;

  const siblingVariations = useMemo(() => {
    if (!path.length) return [] as string[];
    const head = path[0];
    const parentId = model.byId.get(head)?.parent_id;
    if (!parentId) return [];
    return model.variationsOf.get(parentId) ?? [];
  }, [model, path]);

  const step = useCallback(
    (delta: number) => {
      if (!siblingVariations.length) return;
      const idx = siblingVariations.indexOf(path[0]);
      const nextIdx = (idx + delta + siblingVariations.length) % siblingVariations.length;
      const target = siblingVariations[nextIdx < 0 ? 0 : nextIdx];
      if (target) setPath(pathTo(model, target));
    },
    [model, path, siblingVariations],
  );

  return {
    path,
    currentId,
    goTo,
    next,
    previous,
    first,
    last,
    enterVariation,
    exitVariation,
    inVariation,
    nextSiblingVariation: () => step(1),
    prevSiblingVariation: () => step(-1),
  };
}

function pathTo(model: TreeModel, nodeId: string): string[] {
  const out: string[] = [];
  let cur: string | null | undefined = nodeId;
  const seen = new Set<string>();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    out.unshift(cur);
    cur = model.byId.get(cur)?.parent_id;
  }
  return out;
}

export interface MoveListItem {
  nodeId: string;
  san: string;
  number: number;
  side: "w" | "b";
  isCurrent: boolean;
  visited: boolean;
  hasComment: boolean;
  isVariationRoot: boolean;
  nag: string;
}

/** Flatten the *current* path into renderable pairs (1. e4 e5 2. Nf3 ...). */
export function flattenPath(model: TreeModel, path: string[]): MoveListItem[] {
  const visited = new Set(path);
  return path.slice(1).map((id, i) => {
    const node = model.byId.get(id)!;
    return {
      nodeId: id,
      san: node.san ?? "?",
      number: node.move_number,
      side: node.side,
      isCurrent: i === path.length - 2,
      visited: visited.has(id),
      hasComment: (model.commentsByNode.get(id) ?? []).length > 0,
      isVariationRoot: i === 0 && !node.is_mainline,
      nag: (node.nags ?? []).length ? "!" : "",
    };
  });
}

export function variationLine(
  model: TreeModel,
  firstNodeId: string,
  max = 40,
): NodeRow[] {
  const out: NodeRow[] = [];
  let cur: string | undefined = firstNodeId;
  const seen = new Set<string>();
  while (cur && !seen.has(cur) && out.length < max) {
    seen.add(cur);
    const node: NodeRow | undefined = model.byId.get(cur);
    if (!node) break;
    out.push(node);
    cur = node.mainline_child_id ?? undefined;
  }
  return out;
}