import type { WarRoomCorpusTeam } from "./war-room";

export type BattleReadyPasteReport = {
  ready: boolean;
  blocks: number;
  issues: string[];
};

export function inspectBattleReadyPaste(rawPaste: string): BattleReadyPasteReport {
  const blocks = rawPaste
    .replace(/\r\n?/g, "\n")
    .trim()
    .split(/\n\s*\n+/)
    .map((block) => block.trim())
    .filter(Boolean);

  const issues: string[] = [];
  if (blocks.length !== 6) {
    issues.push(`El paste declara ${blocks.length} Pokémon; Sparring exige exactamente 6.`);
  }

  blocks.forEach((block, index) => {
    const label = `Slot ${index + 1}`;
    const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
    const identity = lines[0] ?? "";
    const itemMatch = identity.match(/^(.+?)\s+@\s+(.+)$/);
    if (!itemMatch?.[1]?.trim()) issues.push(`${label}: falta especie/identidad.`);
    if (!itemMatch?.[2]?.trim()) issues.push(`${label}: falta objeto explícito.`);
    if (!lines.some((line) => /^Ability:\s*\S.+$/i.test(line))) issues.push(`${label}: falta habilidad explícita.`);
    if (!lines.some((line) => /^Level:\s*\d+\s*$/i.test(line))) issues.push(`${label}: falta nivel explícito.`);
    if (!lines.some((line) => /^EVs:\s*\S.+$/i.test(line))) issues.push(`${label}: faltan Stat Points/EVs explícitos.`);
    if (!lines.some((line) => /^[A-Za-z][A-Za-z -]*\s+Nature$/i.test(line))) issues.push(`${label}: falta naturaleza explícita.`);
    const moves = lines.filter((line) => /^-\s+\S/.test(line));
    if (moves.length !== 4) issues.push(`${label}: declara ${moves.length} movimientos; se requieren exactamente 4.`);
  });

  return { ready: issues.length === 0, blocks: blocks.length, issues };
}

export function isBattleReadyPaste(rawPaste: string) {
  return inspectBattleReadyPaste(rawPaste).ready;
}

export function sparringCorpusCandidates(teams: WarRoomCorpusTeam[]) {
  return teams.filter((team) =>
    (team.source === "vgcpastes" || team.source === "scouting-library")
    && Boolean(team.savedPasteId || team.pokepasteUrl),
  );
}

export function shuffledSparringCandidates(teams: WarRoomCorpusTeam[], random = Math.random) {
  const pool = [...sparringCorpusCandidates(teams)];
  for (let index = pool.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(random() * (index + 1));
    [pool[index], pool[swapIndex]] = [pool[swapIndex], pool[index]];
  }
  return pool;
}
