export const CHAMPIONS_REGULATION = "M-C";
export const CHAMPIONS_PREVIOUS_REGULATION = "M-B";
export const CHAMPIONS_REGULATION_CACHE_ID = "m-c";
export const CHAMPIONS_REGULATION_STARTED_AT = "2026-09-09T02:00:00.000Z";
export const CHAMPIONS_REGULATION_SOURCE = "https://www.pokemon.com/us/news/get-ready-for-regulation-set-m-c-in-pokemon-champions";
export const CHAMPIONS_SHOWDOWN_COMMIT = "812501ede865eea397cd6e9a6f040d73ff57cc84";
export const CHAMPIONS_CALC_COMMIT = "111407c919c2c886688db704ae97376e768b72e4";
export const SHOWDOWN_SNAPSHOT_SCHEMA = 4;
export const CHAMPIONS_REGULATION_SNAPSHOT_ERROR_CODE = "CHAMPIONS_REGULATION_SNAPSHOT_INCOMPLETE";

// Showdown represents selectable formes as separate IDs. These 35 entries are
// the complete M-B -> M-C legality delta, including regional/cosmetic formes
// and the six newly legal Mega formes.
export const CHAMPIONS_REQUIRED_SPECIES_IDS = Object.freeze([
  "wigglytuff",
  "persian",
  "persianalola",
  "farfetchd",
  "mrmime",
  "swalot",
  "absolmegaz",
  "salamence",
  "salamencemega",
  "garchompmegaz",
  "lucariomegaz",
  "gogoat",
  "golisopod",
  "golisopodmega",
  "rillaboom",
  "cinderace",
  "inteleon",
  "thievul",
  "toxtricity",
  "toxtricitylowkey",
  "grapploct",
  "perrserker",
  "sirfetchd",
  "pincurchin",
  "indeedee",
  "indeedeef",
  "pawmot",
  "arboliva",
  "squawkabilly",
  "squawkabillyblue",
  "squawkabillyyellow",
  "squawkabillywhite",
  "mabosstiff",
  "baxcalibur",
  "baxcaliburmega",
]);

export const CHAMPIONS_REQUIRED_ITEM_IDS = Object.freeze([
  "absolitez",
  "baxcalibrite",
  "garchompitez",
  "golisopite",
  "leek",
  "lucarionitez",
  "salamencite",
  "airballoon",
  "bindingband",
  "ejectbutton",
  "electricseed",
  "grassyseed",
  "mistyseed",
  "normalgem",
  "psychicseed",
  "redcard",
  "rockyhelmet",
  "terrainextender",
]);

// damage-calc landed the six formes before Showdown finalized M-C. Keeping the
// abilities from the regulation source here bridges that short upstream lag;
// the selected base ability is retained only when the Mega forme allows it.
export const CHAMPIONS_M_C_MEGA_ABILITIES = Object.freeze({
  "Absol-Mega-Z": Object.freeze(["Sharpness"]),
  "Salamence-Mega": Object.freeze(["Aerilate"]),
  "Garchomp-Mega-Z": Object.freeze(["Levitate"]),
  "Lucario-Mega-Z": Object.freeze(["Aura Guard"]),
  "Golisopod-Mega": Object.freeze(["Tough Claws"]),
  "Baxcalibur-Mega": Object.freeze(["Thermal Exchange", "Ice Body"]),
});

export function championsRegulationSnapshotIssues(snapshot) {
  const species = new Set(snapshot?.formats?.champions ?? []);
  const items = new Set(snapshot?.itemFormats?.champions ?? []);
  const missingSpecies = CHAMPIONS_REQUIRED_SPECIES_IDS.filter((id) => !species.has(id) || !snapshot?.species?.[id]);
  const missingItems = CHAMPIONS_REQUIRED_ITEM_IDS.filter((id) => !items.has(id) || !snapshot?.items?.[id]);
  return { missingSpecies, missingItems };
}

export function detectChampionsRegulation(snapshot) {
  const { missingSpecies, missingItems } = championsRegulationSnapshotIssues(snapshot);
  return missingSpecies.length === 0 && missingItems.length === 0
    ? CHAMPIONS_REGULATION
    : CHAMPIONS_PREVIOUS_REGULATION;
}

export class ChampionsRegulationSnapshotError extends Error {
  constructor(missingSpecies, missingItems) {
    const details = [
      missingSpecies.length ? `Pokémon: ${missingSpecies.join(", ")}` : "",
      missingItems.length ? `objetos: ${missingItems.join(", ")}` : "",
    ].filter(Boolean).join("; ");
    super(
      `Showdown todavía no expone la Regulación ${CHAMPIONS_REGULATION} completa${details ? ` (${details})` : ""}.`,
    );
    this.name = "ChampionsRegulationSnapshotError";
    this.code = CHAMPIONS_REGULATION_SNAPSHOT_ERROR_CODE;
    this.missingSpecies = [...missingSpecies];
    this.missingItems = [...missingItems];
  }
}

export function isChampionsRegulationSnapshotError(error) {
  return Boolean(
    error
    && typeof error === "object"
    && error.code === CHAMPIONS_REGULATION_SNAPSHOT_ERROR_CODE,
  );
}

export function assertChampionsRegulationSnapshot(snapshot) {
  const { missingSpecies, missingItems } = championsRegulationSnapshotIssues(snapshot);
  const metadataRegulation = snapshot?.metadata?.regulation;
  if (metadataRegulation === CHAMPIONS_REGULATION && missingSpecies.length === 0 && missingItems.length === 0) return;

  throw new ChampionsRegulationSnapshotError(missingSpecies, missingItems);
}
