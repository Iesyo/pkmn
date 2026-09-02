import type { ShowdownSnapshot } from "./showdown-data";

export const SHOWDOWN_SOURCES: Record<string, string>;

export function parseTeambuilderSource(source: string): Record<string, unknown>;

export function parseEs3Export(
  source: string,
  exportName: string,
): Record<string, unknown>;

export function buildShowdownSnapshot(
  fetcher?: typeof fetch,
): Promise<ShowdownSnapshot>;
