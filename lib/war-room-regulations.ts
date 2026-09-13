import { toId } from "./pokemon-data";

export const WAR_ROOM_CURRENT_FORMAT_ID = "champions-m-c";

export const WAR_ROOM_REGULATION_EVIDENCE = [
  {
    formatId: WAR_ROOM_CURRENT_FORMAT_ID,
    formatLabel: "Champions M-C",
    shortLabel: "M-C",
    regulationWeight: 1,
    historical: false,
    setEvidenceEligible: true,
  },
  {
    formatId: "champions-m-b",
    formatLabel: "Champions M-B",
    shortLabel: "M-B",
    regulationWeight: 0.65,
    historical: true,
    setEvidenceEligible: true,
  },
  {
    formatId: "champions-m-a",
    formatLabel: "Champions M-A",
    shortLabel: "M-A",
    regulationWeight: 0.45,
    historical: true,
    setEvidenceEligible: true,
  },
  {
    formatId: "sv-regulation-i",
    formatLabel: "SV Regulation I",
    shortLabel: "SV-I",
    regulationWeight: 0.2,
    historical: true,
    setEvidenceEligible: false,
  },
] as const;

export type WarRoomRegulationEvidence = (typeof WAR_ROOM_REGULATION_EVIDENCE)[number];

export const WAR_ROOM_HISTORICAL_REGULATIONS = WAR_ROOM_REGULATION_EVIDENCE.filter((entry) => entry.historical);

export function getWarRoomRegulationEvidence(formatId: unknown): WarRoomRegulationEvidence | null {
  if (typeof formatId !== "string") return null;
  return WAR_ROOM_REGULATION_EVIDENCE.find((entry) => entry.formatId === formatId) ?? null;
}

export function inferWarRoomRegulationEvidence(format: unknown): WarRoomRegulationEvidence | null {
  if (typeof format !== "string") return null;
  const key = toId(format);
  if (!key) return null;
  if (key === "mc" || key.includes("championsmc")) return WAR_ROOM_REGULATION_EVIDENCE[0];
  if (key === "mb" || key.includes("championsmb")) return WAR_ROOM_REGULATION_EVIDENCE[1];
  if (key === "ma" || key.includes("championsma")) return WAR_ROOM_REGULATION_EVIDENCE[2];
  if (key === "svi" || key === "regulationi" || key.includes("svregulationi")) return WAR_ROOM_REGULATION_EVIDENCE[3];
  return null;
}
