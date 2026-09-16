# Battle Lab: ciclo periódico M-C

`colab/Battle_Lab_MC_Refresh.ipynb` reúne la actualización de datos, BC condicional,
PPO y evaluación directa del candidato. Deriva del patrón operativo 40_017 v3 y
reutiliza los runners LIGHT v3 y holdout ya empleados por Battle Lab.

## Ejecutar

Abrir el notebook en Colab, elegir GPU y ejecutar todas las celdas. El valor por
defecto es LIGHT: 196.608 pasos adicionales y 500 batallas directas por rival,
1.500 en total (productivo, VGC-Bench base y Simple Heuristics). NORMAL añade 786.432 pasos; HARD añade 3.145.728. CENSUS termina
después de convertir las partidas y no entrena ni evalúa.

## Experimento HARD frente al modelo promovido

El piloto HARD solicitado el 2026-09-16 continúa desde el champion BC+PPO
`MC-20260916T200255271236Z`, SHA `5abbed702f2801c8fad33e8bca0df008f51cc113d9007e95bb5fe393961fe3c2`.
Añade **10 épocas BC** si se supera el filtro humano y **3.145.728 pasos PPO**
(16 veces el presupuesto LIGHT). El mismo Colab de Drive queda configurado HARD,
`auto`, RUN_ID vacío, CUDA, 2 entornos, workers automáticos, mínimo BC de 9500 y
500 batallas por rival. Los valores generales del notebook de Git siguen en LIGHT.

Los campos PRODUCTION_CHECKPOINT y PRODUCTION_SHA256 de ese experimento fijan
el modelo promovido como rival directo, además del base y Simple Heuristics.
Esto evita volver a comparar únicamente con LIGHT histórico. Es una referencia
de evaluación explícita y no demuestra qué modelo está cargado en la ROG.
El candidato queda guardado para revisión con PROMOTE_CANDIDATE desactivado.

El ciclo recoge novedades y congela sus datos. Mide el beneficio de continuar
entrenando con HARD desde el champion actual; no es una comparación desde la
misma inicialización y el mismo corpus que el LIGHT anterior. Más entrenamiento
puede mejorar o empeorar el resultado; se interpretan los tres rivales por separado.

Como estimación previa, la última fase PPO LIGHT tardó 1729,36 segundos en L4:
al mismo ritmo, HARD necesitaría unos 7 h 41 min solo de PPO, más preparación,
BC y evaluación (aproximadamente 8 h en total). Cambios de GPU, CPU o duración
de las partidas pueden alterar el tiempo. El progreso real del runner ajusta su ETA.

`report.json` y `report.txt` incluyen tiempos activos observados por fase,
BC+PPO, evaluación y ciclo completo; el tiempo transcurrido separa las pausas
entre sesiones. Los intentos persisten en `status.json`, incluidos los fallidos,
y no se toman tiempos del caché de otra corrida. Se excluyen montaje de Drive
e instalación inicial del notebook. Una desconexión abrupta conserva el último
heartbeat y marca la medición parcial. Para reanudar se mantiene `auto` y la misma
configuración; no seleccionar `new` durante una ejecución incompleta.

Requiere el LIGHT M-C histórico y su split, conservados bajo
`Colabs/LikeNoOneEverWas/BattleLab/MC-Training/`:

- `training/rl/light/seed260913/checkpoints/step-000196608.zip`.
- `data/team-splits/seed-260913/split_manifest.json`, `train/` y `holdout/`.
- Los logs previos de `data/battle_logs/` se incorporan si existen.

El primer champion se identifica por SHA-256
`fa8687d08feeb169f4eb4f4a078b65971346e2ef5b0ca0ff899e811721075759`.
Después se usa exclusivamente `Refresh/champion.json`, comprobando sus bytes.
Un archivo ZIP más reciente no cambia esa selección.

## Datos y separación de equipos

1. Actualizar el CSV Champions de VGCPastes y descargar los pastes publicados.
   Exigir seis sets completos, cuatro movimientos, habilidad, naturaleza y
   reparto de EVs/puntos explícito; validar su legalidad con Showdown M-C.
   Los pastes extra completos pueden dejarse en `Refresh/extra-teams/*.txt`.
2. Consultar las novedades de Showdown BO3 y BO1, incluso si el censo anterior
   alcanzó un tope o agotó el histórico. El presupuesto se aplica por ejecución.
   Guardar por separado `page_budget`, `pagination_stalled`, `source_exhausted`
   y `new_head_complete`; ningún presupuesto agotado equivale a corpus completo.
3. Importar la separación histórica por firma de seis especies y mantener sus
   asignaciones. Las firmas nuevas se asignan de forma determinista, con 20%
   reservado. Las variantes de sets conservan el lado de su firma.
4. Excluir los logs si cualquiera de sus equipos está reservado. Aplicar solo
   ganador, rating ≥1.200 y deduplicación antes de convertir las trayectorias.
   También se registran las firmas vistas en partidas para impedir que pasen
   después del entrenamiento humano a la evaluación mediante un nuevo paste.
5. Ejecutar BC con ≥1.000 trayectorias y el mínimo de transiciones elegido.
   `BC_MIN_TRANSITIONS=9500` habilita el primer piloto humano solicitado con el
   corpus cercano al mínimo anterior (1.056 trayectorias / 9.695 transiciones).
   `10000` mantiene el criterio estándar. Ambos son umbrales operativos, sin
   garantía de calidad; no se rebajan rating, ganador ni exclusión del holdout.
   Con menos datos, el candidato parte directamente del champion para PPO.

El piloto usa una **corrida nueva**, LIGHT, con `BC_MIN_TRANSITIONS=9500`.
El ciclo vuelve a recoger novedades, aplica los filtros y registra el umbral en
su configuración inmutable; no transforma la corrida previa de self-play ni sus
resultados. La fase BC aprende tres épocas y después sigue PPO y el benchmark.
El piloto histórico se comparó indirectamente con el champion; esa corrida no es una
ablación aislada del efecto humano frente al candidato anterior de 76,20%.

BC adapta los lotes a los bloques disponibles y utiliza todas sus transiciones,
incluidos bloques menores de 1.024 y los restos. Cada época registra las
transiciones utilizadas y conserva estado del optimizador y checkpoint.

Los archivos usados por fases completas llevan hashes. Cada ejecución mantiene
su copia de pastes, logs, split y trayectorias. Cambiar esos datos bloquea su
reutilización. El split histórico original también se comprueba en ciclos futuros.
No se conoce todo el corpus de entrenamiento del checkpoint público antecesor.

## Reanudación y recursos

Colab puede mostrar avisos de pip por incompatibilidades con sus paquetes
preinstalados Transformers, Gradio, Diffusers o Google ADK. Este ciclo no los usa.
El fork fijado de poke-env exige websockets 16 y el stack validado conserva
huggingface_hub 0.36.2. No cambiar esas versiones mientras corre el entrenamiento.
Una instalación que termina correctamente y pasa las pruebas estructurales aún
no confirma el runtime de entrenamiento: al iniciar el ciclo se importan las
librerías reales y se comprueba una operación mínima NumPy/PyTorch en el
dispositivo elegido. Si falla, el error se propaga antes de recoger datos.

`RUN_ACTION="auto"` reanuda una ejecución pendiente compatible o empieza una
nueva si la anterior terminó. `new` inicia otro ciclo; `resume` exige uno pendiente.
`RUN_ID` permite señalar una ejecución concreta. La libreta carga el commit exacto
de la ejecución pendiente antes de importar código.

Si PPO ya terminó y falló la evaluación, abrir la libreta actualizada y elegir
`RUN_ACTION="recover_evaluation"`. Dejar `RUN_ID` vacío recupera la corrida activa;
también puede indicarse su ID. Ejecutar todo con el mismo tipo de dispositivo.
Esta acción carga el código corregido, verifica los hashes de datos y modelos y
repite únicamente preparación del motor y evaluación. Conserva el `config.json`
original y registra ambos commits en `recovery/evaluation_config.json`.
Las versiones del runtime deben coincidir con las del entrenamiento guardado.

Los pastes se deduplican con la misma normalización de texto que el benchmark.
Para snapshots antiguos con copias que solo difieren en espacios o saltos de
línea, la evaluación genera `evaluation/corpus/` con equipos únicos, manifiesto
y relación de copias omitidas. El snapshot original permanece intacto; se siguen
rechazando cambios de archivos y cruces de firmas entre train y holdout.

El contrato incluye champion, código, Showdown, VGC-Bench, versiones de runtime,
dispositivo, semilla y parámetros. Una configuración distinta exige otro ciclo.
Los workers de descarga/conversión se recalculan según CPU/memoria (auto90,
máximo ocho); el coordinador escribe los datos. Usar una sola sesión Colab por
esta carpeta: el bloqueo de proceso local no coordina distintas máquinas.

PPO usa por defecto dos entornos paralelos de simulación y CUDA cuando está
disponible. Cada entorno aporta ambas perspectivas de self-play. La evaluación
actual juega una batalla a la vez. `NUM_ENVS` permite 1, 2 o 4 en un ciclo nuevo;
auto90 de descarga/conversión no modifica ese parámetro. Más VRAM ocupada no
implica mayor velocidad: la simulación también depende de CPU y del servidor.
Las nuevas sesiones PPO registran dispositivo real de la política, parámetros,
entornos y pico de memoria CUDA asignada/reservada en logs, estado e informe.
El pico corresponde al proceso actual; no reconstruye el uso de sesiones pasadas
ni mide el porcentaje de actividad GPU. Al terminar el proceso se libera su VRAM.

Cada fase usa un proceso nuevo y conserva stdout, heartbeat y progreso. El ETA
de fase se estima con ciclos anteriores del mismo modo/envs/dispositivo; los
runners muestran además su progreso de pasos o partidas. BC reanuda desde épocas
completas y conserva su optimizador. PPO guarda después de la actualización del
rollout y al terminar. Se excluyen checkpoints incompletos mediante SHA-256.
La reanudación conserva los pesos/progreso; no promete reproducir exactamente
la secuencia aleatoria de entornos y batallas de una ejecución ininterrumpida.

## Evaluación directa: productivo, base y Simple Heuristics

Los ciclos nuevos usan `direct-v1`: el candidato se enfrenta a tres rivales:

- **Productivo**: LIGHT M-C documentado, SHA-256 `fa8687d08feeb169f4eb4f4a078b65971346e2ef5b0ca0ff899e811721075759`.
- **Base**: checkpoint público VGC-Bench BC, revisión `204c76741829ca0681629e41382043c385850d5c`, SHA-256 `57f5edcab415cf6ccc1b6231923c8b66d3b1b7249b6b2562b97531471e4ca60b`.
- **Simple Heuristics**: `SimpleHeuristicsPlayer` del fork de poke-env fijado.

La referencia productiva se resuelve independientemente de `champion.json`:
`PRODUCTION_CHECKPOINT` y `PRODUCTION_SHA256` juntos, después `Refresh/production.json`
si existe (campos `id`, `checkpoint`, `sha256`, `format`), y finalmente el LIGHT
histórico. Seleccionar un champion de entrenamiento no cambia esta referencia.
Se comprueban los bytes antes de jugar. Es la política productiva documentada;
no consulta la ROG ni evalúa la memoria/adaptación personal de Nana.

Cada rival recibe las mismas parejas de equipos holdout. En la segunda partida
se intercambian las políticas, de modo que el candidato juega ambos equipos y
lados. Los modelos controlan Team Preview; Simple Heuristics conserva el preview
del fork. Las decisiones de política son deterministas; el RNG de Showdown es
independiente. `BATTLES_PER_CONTROL=500` ahora significa 500 por rival: 1.500 en total.

Para evaluar pesos ya guardados, usar **`RUN_ACTION="direct_evaluation"`** y el
`RUN_ID` deseado (vacío usa la corrida activa). No recoge datos ni entrena otra vez.
Verifica los hashes y versiones originales y ejecuta solo preparación/evaluación.
Conserva `config.json`, fases, estado e informe originales. El informe nuevo queda
en `runs/<RUN_ID>/direct_evaluation/report.txt`, `report.json` y `comparison.csv`;
`Refresh/latest_direct_run.txt` y `latest_direct_result.json` lo señalan.

Los resultados, bloques y replays están en
`runs/<RUN_ID>/evaluation/direct-v1/<identidad>/`, con carpetas
`replays/production/`, `replays/base/` y `replays/simple-heuristics/`.
Los `.html` se descargan y abren en el navegador. Cada partida del informe incluye
el archivo correspondiente; una interrupción puede dejar replays adicionales de
un bloque incompleto que no se cuentan en el resultado. Solo se reanudan bloques
completos de 20 partidas, validando agenda, pesos, corpus, código y runtime.
Los cambios de contrato crean otra carpeta; nunca reutilizan resultados incompatibles.

## Interpretar y seleccionar

La métrica principal es el score directo contra producción (victoria=1, empate=0.5).
El gate descriptivo marca ventaja observada si supera 50%. Base y Simple Heuristics
se informan por separado: no se promedian rivales distintos para afirmar mejora.
Las corridas históricas conservan su protocolo de comparación indirecta contra
Random, Max Base Power y Simple Heuristics y su gate original. `recover_evaluation`
recupera ese protocolo; `direct_evaluation` genera la nueva prueba por separado.

`MEJORA_OBSERVADA` es una comparación descriptiva: Showdown mantiene RNG
independiente entre modelos y el gate no demuestra significancia estadística ni
nivel contra jugadores humanos. Al reutilizar el grupo reservado para seleccionar
modelos, pasa a actuar como validación. El benchmark histórico separa la muestra de equipos
recién reservados; puede ser pequeña o no existir. El directo informa el número
de equipos holdout reutilizados y no los presenta como una muestra inédita.

Cada ejecución conserva `report.txt`, `report.json`, `comparison.csv`, checkpoints,
chunks y replays sin empaquetarlos. `Refresh/latest_run.txt` y `latest_result.json`
apuntan al último resultado completo. Los checkpoints SB3 usan su formato ZIP
nativo; no se generan bundles ZIP de resultados.

La celda final está desactivada por defecto. Tras una evaluación directa usa su
informe nuevo, no el resultado histórico, para la selección explícita. Activarla después de revisar un PASS
selecciona explícitamente el candidato como champion del siguiente ciclo, siempre
que el champion de referencia no haya cambiado. Registra padre y ejecución en
`promotion.json`. La instalación en ROG y el uso por Nana son pasos separados.

## Validación de esta implementación

Pruebas locales sin GPU/red sobre separación histórica, variantes de pastes,
filtro de ambas perspectivas, duplicados, refresh tras agotamiento, presupuesto
de páginas, contratos de reanudación, hashes, informes y selección explícita.
Se comprueba también la sintaxis de todas las celdas y los tests existentes de
LIGHT v2/v3, entrenamiento, split, censo extendido y benchmark.

El entrenamiento y las 1.500 batallas directas del nuevo protocolo se ejecutan en
Colab. Las pruebas locales no constituyen un resultado de calidad del candidato.
