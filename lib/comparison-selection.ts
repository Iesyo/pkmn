import type { TeamGroup } from "./types";

export const COMPARISON_SELECTION_STORAGE_KEY = "like-no-one-ever-was:comparison:v1";

export interface ComparisonSelection {
  leftVersionId: string;
  rightVersionId: string;
}

export function parseComparisonSelection(raw: string | null): ComparisonSelection | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object") return null;
    const selection = value as Partial<ComparisonSelection>;
    if (typeof selection.leftVersionId !== "string" || !selection.leftVersionId
      || typeof selection.rightVersionId !== "string" || !selection.rightVersionId) return null;
    return { leftVersionId: selection.leftVersionId, rightVersionId: selection.rightVersionId };
  } catch {
    return null;
  }
}

export function readComparisonSelection(storage: Pick<Storage, "getItem">): ComparisonSelection | null {
  try {
    return parseComparisonSelection(storage.getItem(COMPARISON_SELECTION_STORAGE_KEY));
  } catch {
    return null;
  }
}

export function writeComparisonSelection(storage: Pick<Storage, "setItem">, selection: ComparisonSelection) {
  try {
    storage.setItem(COMPARISON_SELECTION_STORAGE_KEY, JSON.stringify(selection));
  } catch {
    // Browser storage may be disabled or full; the current comparison still works.
  }
}

export function resolveComparedVersions(groups: TeamGroup[], leftId: string, rightId: string) {
  const versions = groups.flatMap((group) => group.versions);
  const fallbackLeft = versions[0];
  const fallbackRight = groups.find((group) => group.id !== fallbackLeft?.teamId && group.versions.length)?.versions[0]
    ?? versions[1]
    ?? fallbackLeft;

  return {
    left: versions.find((version) => version.id === leftId) ?? fallbackLeft,
    right: versions.find((version) => version.id === rightId) ?? fallbackRight,
  };
}
