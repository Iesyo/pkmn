import { getMoveData, getSpeciesTypes } from "./pokemon-data";
import type { PokemonSet, PokemonType } from "./types";

export class PasteValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PasteValidationError";
  }
}

function parseHeader(header: string) {
  const itemSeparator = header.lastIndexOf(" @ ");
  const identity = (itemSeparator >= 0 ? header.slice(0, itemSeparator) : header)
    .replace(/\s+\((?:M|F)\)$/i, "")
    .trim();
  const item = itemSeparator >= 0 ? header.slice(itemSeparator + 3).trim() : "";
  const nicknameMatch = identity.match(/^(.*?)\s+\(([^()]+)\)$/);

  if (nicknameMatch) {
    return {
      nickname: nicknameMatch[1].trim(),
      species: nicknameMatch[2].trim(),
      item,
    };
  }

  return { nickname: identity, species: identity, item };
}

export function parseShowdownPaste(paste: string): PokemonSet[] {
  const normalized = paste.replace(/\r\n?/g, "\n").trim();
  if (!normalized) {
    throw new PasteValidationError("Pega un equipo de Pokémon Showdown.");
  }

  const blocks = normalized.split(/\n\s*\n/).filter((block) => block.trim());
  if (blocks.length !== 6) {
    throw new PasteValidationError(
      `El equipo debe contener exactamente 6 Pokémon; encontramos ${blocks.length}.`,
    );
  }

  return blocks.map((block, index) => {
    const lines = block
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    const identity = parseHeader(lines[0]);
    let ability = "";
    let level = 50;
    let teraType: PokemonType | null = null;
    let evs = "";
    let nature = "";
    let dynamaxLevel = 10;
    let gigantamax = false;
    const moves = [] as PokemonSet["moves"];

    for (const line of lines.slice(1)) {
      if (line.startsWith("Ability:")) ability = line.slice(8).trim();
      else if (line.startsWith("Level:")) level = Number(line.slice(6).trim()) || 50;
      else if (line.startsWith("Tera Type:")) teraType = line.slice(10).trim() as PokemonType;
      else if (line.startsWith("Dynamax Level:")) dynamaxLevel = Number(line.slice(14).trim()) || 10;
      else if (line === "Gigantamax: Yes") gigantamax = true;
      else if (line.startsWith("EVs:")) evs = line.slice(4).trim();
      else if (line.endsWith(" Nature")) nature = line.slice(0, -7).trim();
      else if (line.startsWith("- ")) {
        const name = line.slice(2).trim();
        const move = getMoveData(name);
        moves.push({ name, type: move.type, damaging: move.damaging, usage: 0 });
      }
    }

    if (!identity.species) {
      throw new PasteValidationError(`No pudimos leer el Pokémon ${index + 1}.`);
    }
    if (moves.length === 0) {
      throw new PasteValidationError(
        `${identity.species} no contiene movimientos reconocibles.`,
      );
    }

    return {
      id: `paste-${index + 1}`,
      slot: index + 1,
      ...identity,
      ability,
      level,
      teraType,
      mechanics: { dynamaxLevel, gigantamax, megaEvolution: false, zMove: false },
      evs,
      nature,
      moves,
      types: getSpeciesTypes(identity.species),
      performance: {
        games: 0,
        wins: 0,
        leadGames: 0,
        leadWins: 0,
        selectionRate: 0,
      },
    };
  });
}

export async function hashPaste(paste: string) {
  const bytes = new TextEncoder().encode(paste.replace(/\r\n?/g, "\n").trim());
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}
