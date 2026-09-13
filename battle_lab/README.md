# Battle Lab

El Battle Lab se ejecutará por completo en Google Colab. La computadora del
usuario solamente abre el notebook y, en fases posteriores, la interfaz
temporal de Gradio.

## Fase 1: motor Showdown

Esta fase comprueba el camino técnico que usarán VGC-Bench y los futuros
agentes:

1. obtiene la revisión oficial de Pokémon Showdown fijada por
   `lib/champions-regulation.mjs`;
2. instala y compila Showdown dentro del disco temporal de Colab;
3. configura el servidor para escuchar únicamente en `127.0.0.1`;
4. valida dos equipos Champions M-C y comprueba que un control con Stat Points
   ilegales sea rechazado;
5. ejecuta 20 combates reales por WebSocket entre `MaxBasePowerPlayer` y
   `RandomPlayer`, usando el fork de `poke-env` requerido por VGC-Bench;
6. guarda métricas, resultados y replays comprimidos.

No requiere GPU. El primer arranque instala las dependencias de Showdown y es
el más lento; los reintentos dentro del mismo runtime reutilizan la instalación.
Todas las operaciones largas y los combates muestran progreso, tiempo
transcurrido y ETA.

El notebook instala `poke-env` en un entorno virtual dentro del runtime, sin
alterar el Python global de Colab. La instalación muestra su salida completa y
reintenta fallos transitorios de GitHub o PyPI hasta tres veces.

## Ejecutar en Colab

[Abrir Battle Lab Fase 1 en Colab](https://colab.research.google.com/github/Iesyo/pkmn/blob/main/colab/Battle_Lab_Phase_1.ipynb)

El notebook utiliza estas rutas:

```text
/content/battle-lab-runtime/                         runtime temporal
/content/drive/MyDrive/Pokemon VGC/BattleLab/
└── results/
    └── phase-1/                                    JSON y ZIP persistentes
```

Google Drive solo recibe el JSON final y un ZIP por ejecución. Showdown,
`node_modules` y los archivos intermedios permanecen en `/content` para evitar
miles de lecturas y escrituras pequeñas sobre Drive.

## Ejecutar fuera de Colab

Requiere Python 3.10 o posterior, Node.js 18 o posterior, Git y npm.

```bash
python -m venv .venv-battle-lab
source .venv-battle-lab/bin/activate
python -m pip install -r battle_lab/requirements-phase1.txt
python battle_lab/showdown_smoke.py --battles 20
```

El checkout de Showdown se crea en `.battle-lab-runtime/` y nunca reemplaza
una instalación ajena. El puerto interno predeterminado es `8000`; si ya está
ocupado, el runner se detiene sin matar el proceso que lo utiliza.

## Salida del smoke test

Cada ejecución produce:

- `phase1-<timestamp>.json`: versión de Showdown, formato, equipos, validación,
  entorno, duración, resultados por combate y rendimiento;
- `phase1-<timestamp>-replays.zip`: replays HTML y logs de la ejecución.

El servidor usa `--no-security` porque solo escucha en loopback. El puerto de
Showdown no debe publicarse mediante Gradio; más adelante Gradio será la única
superficie accesible desde Internet.
