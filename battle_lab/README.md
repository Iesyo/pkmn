# Battle Lab

El Battle Lab se ejecuta por completo en Google Colab. La computadora del
usuario solo abre la libreta y, en fases posteriores, la interfaz temporal de
Gradio.

## Fase 2: benchmark VGC-Bench sin piedad

La libreta canónica mide a **VGC-Bench contra los tres baselines usados por el
proyecto original** y conserva el self-play como modo alternativo:

1. fija y compila la revisión oficial de Pokémon Showdown seleccionada por
   `lib/champions-regulation.mjs`;
2. verifica por SHA-256 y valida un corpus Champions M-C de equipos públicos
   reales, además de cualquier `.txt` del Team Builder guardado en Drive;
3. fija el código oficial de
   [VGC-Bench](https://github.com/cameronangliss/vgc-bench) en un commit exacto,
   sin descargar su submódulo de Showdown;
4. descarga el checkpoint público final `BC seed1 / epoch 100` desde
   [vgc-bench-models](https://huggingface.co/cameronangliss/vgc-bench-models)
   y verifica sus bytes con SHA-256;
5. usa la política neuronal para Team Preview y para cada turno de VGC-Bench;
6. elige siempre la acción legal de mayor probabilidad (`deterministic=True`);
7. usa CUDA automáticamente cuando Colab ofrece GPU y cae a CPU si no;
8. enfrenta al modelo con `RandomPlayer`, `MaxBasePowerPlayer` y
   `SimpleHeuristicsPlayer` usando la misma agenda espejada;
9. rota cruces balanceados sin repetir pareja hasta agotar el round-robin;
10. guarda métricas reproducibles, Elo interno y replays comprimidos.

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
- el modelo neuronal siempre usa toda la red; los rivales deliberadamente más
  simples permiten medir cuánto aporta.

El checkpoint publicado fue entrenado con equipos M-A/M-B. M-C usa el mismo
espacio de reglas, pero sigue siendo una evaluación fuera de su distribución
de entrenamiento. El benchmark cuantifica su rendimiento en M-C sin asumir que
equivale a su fuerza en las regulaciones de entrenamiento.

## Benchmark calibrado

Con `RUN_EVALUATION = True`, el Colab ejecuta **500 combates contra cada baseline**
en el modo configurado:

- `RandomPlayer`: escoge acciones legales al azar;
- `MaxBasePowerPlayer`: prioriza potencia base, con objetivos y cambios simples;
- `SimpleHeuristicsPlayer`: usa la heurística de dobles de `poke-env`, que puntúa
  daño, precisión, STAB, tipos, HP, boosts y cambios.

Son 1,500 combates en total. Cada pareja de Teams se juega dos veces: VGC-Bench
usa un Team/lado en la primera y el Team/lado contrario en la segunda. Los tres
rivales reciben exactamente la misma agenda, cuyo SHA-256 queda en el JSON.
Los baselines usan Team Preview aleatorio, como define `poke-env`; VGC-Bench usa
su Team Preview aprendido.

Después de completar la tanda, el valor predeterminado vuelve a
`RUN_EVALUATION = False` para que `Ejecutar todo` reutilice el resultado más
reciente y genere la auditoría sin gastar otros 25 minutos.

El resultado incluye puntuación de match, diferencia Elo contra cada baseline,
intervalo Wilson del 95% y un Elo de rendimiento contra el pool equiponderado.
La escala usa la curva logística Elo-400 y ancla internamente cada rival en
1500. Es útil para comparar checkpoints y ejecuciones de Battle Lab, pero **no
es el rating oficial de Pokémon Showdown**.

## Auditoría de replays y pares espejo

Después de una ejecución completa, el Colab puede reutilizar el JSON y ZIP más
recientes sin repetir los 1,500 combates. Por defecto audita `MC182` contra
`SimpleHeuristicsPlayer` y selecciona 20 derrotas representativas, cubriendo
primero rivales distintos.

`battle_lab/audit_benchmark.py` relaciona cada derrota con el combate espejo,
recupera ambos leads, Pokémon observados, movimientos, primera baja, cambios,
protecciones, inmunidades, fallos y mecánicas especiales. Los casos reciben una
prioridad reproducible; una señal automática sirve para localizar partidas,
pero no se presenta como prueba de que una jugada fue tácticamente incorrecta.

La auditoría genera:

- `audit.json`: evidencia estructurada, cobertura y limitaciones;
- `report.html`: tablero navegable con enlaces a los casos seleccionados;
- `report.md`: versión legible y portable;
- `cases.csv`: todas las derrotas ordenadas por prioridad;
- `replays/`: copia de cada replay seleccionado y de su espejo.

El protocolo de Showdown no conserva logits, valoración de estado ni acciones
descartadas. Tampoco revela un Pokémon seleccionado que nunca entró al campo.
Si el análisis de replays no explica un patrón, el siguiente nivel será una
repetición instrumentada de los cruces prioritarios.

## Corpus competitivo M-C

La ejecución normal ya no usa los dos fixtures sintéticos del smoke test. Usa
un snapshot versionado de **12 equipos públicos reales** del
[VGCPastes Repository](https://docs.google.com/spreadsheets/d/1axlwmzPA49rYkqXh7zHvAtSP-TKbM0ijGYBPRflLSWw/edit#gid=2001945654),
capturado el 13 de septiembre de 2026. La selección incluye equipos con puesto
en ladder o participación identificada en eventos como Tera Square Offline
Meetup #2, Tsuk Off #26, Alpensee Tour #74, Sitrus Series #75 y el r/VGC
Regulation M-C Kickoff Cup.

El manifiesto conserva para cada equipo: ID de VGCPastes, autor/jugador,
evento, puesto, roster, PokéPaste, fuente original, archivo y SHA-256. Showdown
vuelve a validar los 12 antes de cada tanda; si cualquiera de los versionados
deja de ser legal, la ejecución se detiene en vez de sustituirlo en silencio.

Con 12 equipos existen 66 parejas distintas. El scheduler usa round-robin
sembrado y equilibra tanto apariciones como lados Alpha/Beta. Por ello los 20
combates predeterminados usan 20 cruces distintos y cada equipo aparece tres o
cuatro veces. A partir del combate 67 se inicia un nuevo ciclo reproducible.

## Ejecutar en Colab

[Abrir Battle Lab en Colab](https://colab.research.google.com/github/Iesyo/pkmn/blob/main/colab/Battle_Lab.ipynb)

La libreta mantiene el runtime pesado en `/content` y persiste solo los
resultados:

```text
/content/battle-lab-runtime/                         runtime temporal
/content/drive/MyDrive/Pokemon VGC/BattleLab/
├── teams/                                          exports .txt del Team Builder
└── results/
    └── phase-2/                                    JSON y ZIP persistentes
        └── audits/                                 reportes y replays prioritarios
```

En la web, abre **Exportar → Descargar .txt** y sube ese archivo a la carpeta
`teams/`. La siguiente ejecución de Colab lo descubre, verifica que tenga seis
sets, elimina duplicados exactos, lo valida con Showdown y lo incorpora a la
rotación. Los `.txt` inválidos se enumeran en `teams.ignored` sin impedir que
los demás jueguen.

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
python battle_lab/vgc_bench_battle.py \
  --mode benchmark \
  --benchmark-battles-per-baseline 500 \
  --device auto
```

Para añadir una carpeta de equipos exportados en formato Showdown al corpus:

```bash
python battle_lab/vgc_bench_battle.py \
  --extra-teams-dir ruta/equipos \
  --battles 20
```

Para una prueba diagnóstica de una sola pareja, `--team-a` y `--team-b` siguen
disponibles juntos; ese modo desactiva la rotación.

Para regresar al combate neuronal contra sí mismo:

```bash
python battle_lab/vgc_bench_battle.py \
  --mode self-play \
  --battles 20 \
  --device auto
```

Para auditar un benchmark ya terminado:

```bash
python battle_lab/audit_benchmark.py \
  --result-json ruta/vgc-bench-<timestamp>.json \
  --replays-zip ruta/vgc-bench-<timestamp>-replays.zip \
  --output-dir ruta/audits/mc182 \
  --team-id MC182 \
  --baseline simple-heuristics \
  --sample-size 20
```

El checkout de Showdown, el de VGC-Bench y el modelo se crean dentro de
`.battle-lab-runtime/`; nunca reemplazan instalaciones ajenas. El puerto
interno predeterminado es `8000`. Si ya está ocupado, el runner se detiene sin
matar el proceso que lo utiliza.

## Artefactos verificables

Cada ejecución produce:

- `vgc-bench-<timestamp>.json`: commits, hash del checkpoint, dispositivo,
  versión de PyTorch/SB3, dimensiones del modelo, procedencia y hash de cada
  equipo, agenda, resultados por baseline, Elo interno, victorias por equipo,
  rendimiento y correcciones de alias;
- `vgc-bench-<timestamp>-replays.zip`: replays HTML y logs.
- `audits/<run>-<team>-<baseline>/`: informe HTML/Markdown, JSON, CSV y replays
  seleccionados junto con sus espejos.

La Fase 1 se conserva en `battle_lab/showdown_smoke.py` como diagnóstico ligero
del motor. El servidor usa `--no-security` porque solo escucha en loopback.
Nunca debe publicarse el puerto de Showdown mediante Gradio.
