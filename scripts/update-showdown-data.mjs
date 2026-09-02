import { mkdir, writeFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";

import { buildShowdownSnapshot } from "../lib/showdown-snapshot-builder.mjs";

const snapshot = await buildShowdownSnapshot();

await mkdir(new URL("../public/data/", import.meta.url), { recursive: true });
await writeFile(
  new URL("../public/data/showdown-dex.json.gz", import.meta.url),
  gzipSync(`${JSON.stringify(snapshot)}\n`, { level: 9 }),
);

console.log(
  `Snapshot creado: ${Object.keys(snapshot.species).length} especies, ${Object.keys(snapshot.moves).length} movimientos y ${Object.keys(snapshot.items).length} objetos.`,
);
