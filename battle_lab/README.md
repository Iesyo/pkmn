# Battle Lab

El Battle Lab se ejecuta por completo en Google Colab. La computadora del
usuario solo abre la libreta y, en fases posteriores, la interfaz temporal de
Gradio.

## Fase 2: VGC-Bench sin piedad

La libreta canónica ahora ejecuta combates reales **VGC-Bench vs. VGC-Bench**:

1. fija y compila la revisión oficial de Pokémon Showdown seleccionada por
   `lib/champions-regulation.mjs`;
2. valida dos equipos Champions M-C y confirma que Showdown rechaza un control
   con Stat Points ilegales;
3. fija el código oficial de
   [VGC-Bench](https://github.com/cameronangliss/vgc-bench) en un commit exacto,
   sin descargar su submódulo de Showdown;
4. descarga el checkpoint público final `BC seed1 / epoch 100` desde
   [vgc-bench-models](https://huggingface.co/cameronangliss/vgc-bench-models)
   y verifica sus bytes con SHA-256;
5. usa la política neuronal para Team Preview y para cada turno en ambos lados;
6. elige siempre la acción legal de mayor probabilidad (`deterministic=True`);
7. usa CUDA automáticamente cuando Colab ofrece GPU y cae a CPU si no;
8. guarda métricas reproducibles y replays comprimidos.

El adaptador corrige además un caso del fork de `poke-env`: Open Team Sheets
puede registrar dos alias de una misma forma (`Indeedee` e `Indeedee-F`) y crear
una observación de 13 Pokémon. Battle Lab colapsa únicamente esos alias bajo
Species Clause, conserva el orden real de los seis slots y exige la forma
exacta de entrada del modelo: 6,936 valores y dos ramas de 107 acciones.

### Qué significa «todo su poder»

- checkpoint público más reciente y fijado por commit/hash;
- Team Preview aprendido, no aleatorio;
- inferencia determinista, sin muestreo de acciones;
- máscara de legalidad de VGC-Bench;
- GPU si está disponible;
- ambos jugadores usan la red, no `RandomPlayer` ni `MaxBasePowerPlayer`.

El checkpoint publicado fue entrenado con equipos M-A/M-B. M-C usa el mismo
espacio de reglas, pero sigue siendo una evaluación fuera de su distribución
de entrenamiento. Es una base mucho más seria que los bots del smoke test; su
Elo en M-C se medirá después contra baselines y rivales calibrados, no se
presupone.

## Ejecutar en Colab

[Abrir Battle Lab en Colab](https://colab.research.google.com/github/Iesyo/pkmn/blob/main/colab/Battle_Lab.ipynb)

La libreta mantiene el runtime pesado en `/content` y persiste solo los
resultados:

```text
/content/battle-lab-runtime/                         runtime temporal
/content/drive/MyDrive/Pokemon VGC/BattleLab/
└── results/
    └── phase-2/                                    JSON y ZIP persistentes
```

`DEVICE = "auto"` es el valor recomendado. La instalación de
`stable-baselines3` se hace sin dependencias dentro del directorio aislado para
reutilizar PyTorch/CUDA del runtime de Colab y evitar descargar una segunda
pila CUDA de varios gigabytes.

## Ejecutar fuera de Colab

Requiere Python 3.10 o posterior, Node.js 24 o posterior, Git, npm y una
instalación de PyTorch apropiada para el equipo.

```bash
python -m venv .venv-battle-lab
source .venv-battle-lab/bin/activate
python -m pip install -r battle_lab/requirements-phase1.txt
python -m pip install -r battle_lab/requirements-phase2.txt
python battle_lab/vgc_bench_battle.py --battles 20 --device auto
```

Para usar otros equipos exportados en formato Showdown:

```bash
python battle_lab/vgc_bench_battle.py \
  --team-a ruta/equipo-a.txt \
  --team-b ruta/equipo-b.txt \
  --battles 20
```

El checkout de Showdown, el de VGC-Bench y el modelo se crean dentro de
`.battle-lab-runtime/`; nunca reemplazan instalaciones ajenas. El puerto
interno predeterminado es `8000`. Si ya está ocupado, el runner se detiene sin
matar el proceso que lo utiliza.

## Artefactos verificables

Cada ejecución produce:

- `vgc-bench-<timestamp>.json`: commits, hash del checkpoint, dispositivo,
  versión de PyTorch/SB3, dimensiones del modelo, equipos, victorias,
  rendimiento y correcciones de alias;
- `vgc-bench-<timestamp>-replays.zip`: replays HTML y logs.

La Fase 1 se conserva en `battle_lab/showdown_smoke.py` como diagnóstico ligero
del motor. El servidor usa `--no-security` porque solo escucha en loopback.
Nunca debe publicarse el puerto de Showdown mediante Gradio.
