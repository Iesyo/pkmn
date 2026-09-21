import { mkdir, readdir, rm, writeFile } from "node:fs/promises";

// Pokémon Champions dibuja el Team Preview con sus propios menu sprites: mismo
// arte, misma pose y siempre iguales. Tenerlos en el repo es lo que permite
// identificar al rival comparando la silueta en vez de deducirla de los tipos.
const CATEGORY = "Category:Champions menu sprites";
const ARCHIVES_API = "https://archives.bulbagarden.net/w/api.php";
const POKEDEX_URL = "https://play.pokemonshowdown.com/data/pokedex.json";
const AGENT = "pkmn-vgc-champions/1.0 (actualizacion de datos; repo Iesyo/pkmn)";

const spriteDirectory = new URL("../public/data/champions-sprites/", import.meta.url);
const manifestPath = new URL("../public/data/champions-sprites.json", import.meta.url);

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function fetchJson(url) {
  const response = await fetch(url, { headers: { "User-Agent": AGENT } });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} al pedir ${url}`);
  }
  return response.json();
}

async function categoryFiles() {
  const files = new Map();
  let cont = {};
  for (;;) {
    const params = new URLSearchParams({
      action: "query",
      generator: "categorymembers",
      gcmtitle: CATEGORY,
      gcmtype: "file",
      gcmlimit: "200",
      prop: "imageinfo",
      iiprop: "url",
      format: "json",
      ...cont,
    });
    const data = await fetchJson(`${ARCHIVES_API}?${params}`);
    for (const page of Object.values(data?.query?.pages ?? {})) {
      const [info] = page.imageinfo ?? [];
      if (info) {
        files.set(page.title.replace(/^File:/, ""), info.url);
      }
    }
    if (!data.continue) {
      return files;
    }
    cont = data.continue;
    await sleep(400);
  }
}

// Los nombres de archivo llevan tildes ("Poké Ball") y Showdown no; hay que
// quitarlas, no borrar la letra, o "pokball" no casa con "pokeball".
const key = (text) =>
  text
    .normalize("NFD")
    .replaceAll(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replaceAll(/[^a-z0-9]/g, "");

// Champions nombra algunas formas distinto que Showdown. Comprobado mirando
// los sprites uno a uno: "-Female" es la hembra, "-Jumbo" es la talla que
// Showdown llama Super, y en Maushold están al revés — la base de Champions
// es la familia de cuatro y "-Three" la de tres.
// Un valor con varias especies significa que Champions dibuja una sola cosa
// para todas ellas: el mega de Meowstic es el mismo para macho y hembra.
const FORME_NAMES = new Map([
  ["678|female", "Meowstic-F"],
  ["678|mega", ["Meowstic-M-Mega", "Meowstic-F-Mega"]],
  ["855|", ["Polteageist", "Polteageist-Antique"]],
  ["1013|", ["Sinistcha", "Sinistcha-Masterpiece"]],
  ["711|jumbo", "Gourgeist-Super"],
  ["876|female", "Indeedee-F"],
  ["902|female", "Basculegion-F"],
  ["925|", "Maushold-Four"],
  ["925|three", "Maushold"],
]);

async function speciesByDexEntry() {
  const pokedex = await fetchJson(POKEDEX_URL);
  const byEntry = new Map();
  for (const entry of Object.values(pokedex)) {
    if (!entry?.name || !(entry.num > 0)) {
      continue;
    }
    byEntry.set(`${entry.num}|${key(entry.forme ?? "")}`, entry.name);
  }
  return byEntry;
}

const files = await categoryFiles();
const byEntry = await speciesByDexEntry();

const sprites = {};
const unmatched = [];
const cosmetic = [];
for (const [title, url] of [...files].sort(([left], [right]) => left.localeCompare(right))) {
  const match = /^Menu CP (\d{4})(?:-(.+))?\.png$/.exec(title);
  if (!match) {
    unmatched.push(`${title}: nombre inesperado`);
    continue;
  }
  const number = Number(match[1]);
  const forme = key(match[2] ?? "");
  // Vivillon, Furfrou y compañía: variantes sólo cosméticas que Showdown trata
  // como una única especie, así que todas alimentan al mismo candidato. El
  // respaldo se anota para que un nombre de forma que no cuadre se vea en la
  // salida en vez de acabar mudo en la especie base.
  const exact = FORME_NAMES.get(`${number}|${forme}`) ?? byEntry.get(`${number}|${forme}`);
  const fallback = exact ?? byEntry.get(`${number}|`);
  if (!fallback) {
    unmatched.push(`${title}: sin especie para el número ${number}`);
    continue;
  }
  if (!exact && forme) {
    cosmetic.push(`${title} -> ${fallback}`);
  }
  const name = title.replaceAll(" ", "_");
  for (const species of Array.isArray(fallback) ? fallback : [fallback]) {
    (sprites[species] ??= []).push(name);
  }
  const response = await fetch(url, { headers: { "User-Agent": AGENT } });
  if (!response.ok) {
    throw new Error(`No se pudo bajar ${title}: ${response.status}`);
  }
  await mkdir(spriteDirectory, { recursive: true });
  await writeFile(new URL(name, spriteDirectory), Buffer.from(await response.arrayBuffer()));
  await sleep(150);
}

if (unmatched.length) {
  throw new Error(`Sprites sin mapear:\n  ${unmatched.join("\n  ")}`);
}

if (cosmetic.length) {
  console.log(
    `Formas sin equivalente exacto en Showdown, plegadas sobre la base (${cosmetic.length}):`,
  );
  for (const line of cosmetic) {
    console.log(`  ${line}`);
  }
}

// Deja fuera lo que ya no esté en la categoría, para que el directorio sea
// exactamente lo que describe el manifiesto.
const expected = new Set(Object.values(sprites).flat());
for (const name of await readdir(spriteDirectory)) {
  if (!expected.has(name)) {
    await rm(new URL(name, spriteDirectory));
  }
}

await writeFile(
  manifestPath,
  `${JSON.stringify(
    {
      source: `Bulbagarden Archives, ${CATEGORY}`,
      captured: new Date().toISOString().slice(0, 10),
      directory: "champions-sprites",
      sprites: Object.fromEntries(
        Object.entries(sprites).sort(([left], [right]) => left.localeCompare(right)),
      ),
    },
    null,
    1,
  )}\n`,
);

console.log(
  `Sprites de Champions actualizados: ${Object.keys(sprites).length} especies en ${expected.size} archivos.`,
);
