import { writeFile } from "node:fs/promises";

// Keep synchronous paste parsing and stored Teams in step with the bundled
// Showdown snapshot. The full Pokédex is loaded separately by the Builder.
export async function writePokemonTypeIndex(snapshot) {
  const species = Object.fromEntries(Object.entries(snapshot.species)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([id, entry]) => [id, entry.championsOverride?.types ?? entry.types]));
  const moves = Object.fromEntries(Object.entries(snapshot.moves)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([id, entry]) => [
      id,
      [entry.championsOverride?.type ?? entry.type, (entry.championsOverride?.category ?? entry.category) !== "Status"],
    ]));

  await writeFile(new URL("../lib/pokemon-type-index.json", import.meta.url), `${JSON.stringify({ species, moves })}\n`);
}
