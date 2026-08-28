# Like No One Ever Was 🔥

Laboratorio personal para guardar, versionar y comparar el rendimiento
histórico de equipos de Pokémon VGC.

## Primera entrega

- Teams con Pokepaste y versiones inmutables.
- Comparador simétrico Team A vs Team B.
- Fichas de seis Pokémon con set, uso y rendimiento.
- Leads frecuentes y mejores leads.
- Cobertura, debilidades, resistencias, inmunidades y puntos ciegos.
- Vista defensiva base y Tera separadas.
- Registro de partidas con replay, cuatro picks, dos leads y el equipo rival visto.
- Best/Worst Matchups y Highest/Lowest Attendance calculados por Pokémon rival.
- Historial vinculado a la versión exacta del equipo.
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
```

La arquitectura completa está en [docs/architecture.md](docs/architecture.md).

## Desarrollo del sitio

Requiere Node.js `>=22.13.0`.

```bash
npm run dev
npm run lint
npm test
```

Para generar una migración después de cambiar `db/schema.ts`:

```bash
npm run db:generate
```

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

La base local se crea en `data/pkmn.db`. Usa `PKMN_DB_PATH` para cambiar la
ruta.

## Principio del producto

La aplicación describe lo que ya ocurrió. No predice resultados ni sustituye
el criterio de juego. Una modificación del equipo crea `v2`, `v3`, etc.; jamás
reescribe la versión utilizada por una partida anterior.
