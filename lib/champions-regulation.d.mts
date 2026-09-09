export const CHAMPIONS_REGULATION: "M-C";
export const CHAMPIONS_PREVIOUS_REGULATION: "M-B";
export const CHAMPIONS_REGULATION_CACHE_ID: "m-c";
export const CHAMPIONS_REGULATION_STARTED_AT: "2026-09-09T02:00:00.000Z";
export const CHAMPIONS_REGULATION_SOURCE: string;
export const CHAMPIONS_SHOWDOWN_COMMIT: string;
export const CHAMPIONS_CALC_COMMIT: string;
export const SHOWDOWN_SNAPSHOT_SCHEMA: 4;
export const CHAMPIONS_REQUIRED_SPECIES_IDS: readonly string[];
export const CHAMPIONS_REQUIRED_ITEM_IDS: readonly string[];
export const CHAMPIONS_M_C_MEGA_ABILITIES: Readonly<Record<string, readonly string[]>>;

export function championsRegulationSnapshotIssues(snapshot: unknown): {
  missingSpecies: string[];
  missingItems: string[];
};

export function detectChampionsRegulation(snapshot: unknown): "M-C" | "M-B";
export function assertChampionsRegulationSnapshot(snapshot: unknown): void;
