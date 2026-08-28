# Núcleo Python

Este paquete contiene la lógica de dominio del proyecto: lectura de equipos de
Pokémon Showdown, versionado inmutable, registro de partidas y persistencia
SQLite.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e "backend[dev]"
uvicorn pkmn_vgc.api:app --app-dir backend --reload
```

La base se crea en `data/pkmn.db`. Se puede cambiar con `PKMN_DB_PATH`.
