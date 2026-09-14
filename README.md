# Like No One Ever Was 🔥

Laboratorio personal para guardar, versionar y comparar el rendimiento
histórico de equipos de Pokémon VGC.

## Primera entrega

- Teams con Pokepaste y versiones inmutables.
- Team Builder con seis slots, Pokémon Champions y formatos Gen 6–9, mecánicas especiales y cobertura en vivo.
- Pokédex local generada desde Pokémon Showdown/Smogon: tipos y stats base oficiales, habilidades ligadas a la especie, movimientos filtrados por learnset y objetos disponibles por formato.
- Selector de Stat Points de Champions (32 por stat, 66 totales) con cálculo final a nivel 50; los formatos clásicos conservan EVs 252/510.
- Comparador simétrico Team A vs Team B.
- Fichas de seis Pokémon con set, uso y rendimiento.
- Leads frecuentes y mejores leads.
- Cobertura, debilidades, resistencias, inmunidades y puntos ciegos.
- Vista defensiva base y Tera separadas.
- Registro de partidas con replay, cuatro picks, dos leads y el equipo rival visto.
- Best/Worst Matchups y Highest/Lowest Attendance calculados por Pokémon rival.
- Historial vinculado a la versión exacta del equipo.
- War Room separado de Scouting para auditar un Team contra M-C, preparar los
  mejores cuatro/lead/backline ante un rival y explorar cambios manteniendo un
  core bloqueado. El optimizador permite proteger por integrante la identidad,
  objeto, habilidad, naturaleza, Stat Points y cada slot de movimiento. Teams y
  Team Builder pueden enviar una versión o borrador directamente; se bloquean
  hasta cinco identidades y Partner Search ofrece hasta 12 alternativas por
  ronda, con reemplazo reversible y recálculo sobre el borrador actualizado.
  Si el lote del core se agota, completa la ronda desde el corpus ampliado sin
  repetir candidatos; cada alta recibe un set completo de Battle Data o un
  respaldo legal determinista, nunca una tarjeta vacía.
- Recomendaciones explicables con tres niveles de evidencia: set exacto, team
  preview y frecuencia del corpus. Sus índices ordenan alternativas; nunca se
  presentan como probabilidad de victoria.
- El archivo público alimenta frecuencias y cores; los rivales guardados en
  Mis pastes se suman al selector con su set exacto sin inflar esas frecuencias.
- Núcleo Python + SQLite para uso personal.
- Adaptador D1 (SQLite-compatible) para la versión alojada.

## Estructura

```text
app/                 interfaz y rutas del sitio
components/vgc/      componentes del dashboard
db/                  esquema y acceso D1
backend/pkmn_vgc/    núcleo Python, FastAPI y SQLite
docs/                decisiones técnicas
tests/               pruebas del sitio y del parser TypeScript
backend/tests/       pruebas del dominio Python
battle_lab/          runners y contratos del Battle Lab en Colab
colab/               notebooks reproducibles
```

La arquitectura completa está en [docs/architecture.md](docs/architecture.md).

## Desarrollo del sitio

Requiere Node.js `>=22.13.0`.

```bash
npm ci
npm run dev
npm run lint
npm test
```

`npm run dev` prepara y actualiza automáticamente la base SQLite/D1 local
antes de iniciar el sitio. Los datos de desarrollo permanecen en
`.wrangler/state/` entre reinicios.

En Windows PowerShell, si la política del sistema bloquea `npm.ps1`, usa los
ejecutables `.cmd` sin modificar la política de seguridad:

```powershell
npm.cmd ci
npm.cmd run dev
```

Para generar una migración después de cambiar `db/schema.ts`:

```bash
npm run db:generate
```

Para actualizar el snapshot de Pokémon Showdown usado por el Team Builder:

```bash
npm run data:showdown
```

Durante una transición de regulación en la que el CDN público todavía no esté actualizado, puede generarse desde un checkout oficial de `pokemon-showdown` ya compilado:

```bash
npm run data:showdown:checkout -- ../pokemon-showdown
```

Ambos caminos validan la regulación vigente antes de reemplazar el archivo. El snapshot se guarda comprimido en `public/data/showdown-dex.json.gz`, por lo que la interfaz sigue funcionando sin consultar servicios externos durante cada edición y descarga menos de 600 KB.

## Núcleo Python

Requiere Python `>=3.12`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e "backend[dev]"
uvicorn pkmn_vgc.api:app --app-dir backend --reload
```

Sin instalar dependencias externas se pueden ejecutar las pruebas del parser y
repositorio porque usan `unittest` y `sqlite3` de la biblioteca estándar:

```bash
PYTHONPATH=backend python -m unittest discover -s backend/tests -t .
```

## Battle Lab en Colab

La libreta canónica levanta una instancia privada de Pokémon Showdown, valida
Champions M-C y mide el checkpoint final de VGC-Bench contra `RandomPlayer`,
`MaxBasePowerPlayer` y `SimpleHeuristicsPlayer` mediante cruces espejados. Usa
GPU automáticamente cuando está disponible e incluye barra de progreso, ETA,
Elo interno verificable y replays en Google Drive. El self-play permanece como
modo alternativo. También puede reutilizar una tanda ya terminada para auditar
un equipo: cruza derrotas con sus combates espejo, extrae señales observables
del protocolo de Showdown y genera un informe HTML/JSON/CSV sin volver a jugar
las 1,500 partidas.

[Abrir Battle Lab en Colab](https://colab.research.google.com/github/Iesyo/pkmn/blob/main/colab/Battle_Lab.ipynb)

La implementación y las rutas están documentadas en
[battle_lab/README.md](battle_lab/README.md).

La base local se crea en `data/pkmn.db`. Usa `PKMN_DB_PATH` para cambiar la
ruta.

## Principio del producto

La aplicación separa observación y decisión: Scouting documenta lo que existe
o ya ocurrió; War Room transforma esa evidencia en recomendaciones auditables.
No predice resultados ni sustituye el criterio de juego. Cambiar una especie o
el formato crea `v2`, `v3`, etc.; ajustar únicamente un set crea `v1.01`,
`v1.02`, etc. Jamás se reescribe la versión utilizada por una partida anterior.
