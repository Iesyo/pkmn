# Battle Lab: ciclo periódico M-C

`colab/Battle_Lab_MC_Refresh.ipynb` reúne la actualización de datos, BC condicional,
PPO y comparación champion/candidato. Deriva del patrón operativo 40_017 v3 y
reutiliza los runners LIGHT v3 y holdout ya empleados por Battle Lab.

## Ejecutar

Abrir el notebook en Colab, elegir GPU y ejecutar todas las celdas. El valor por
defecto es LIGHT: 196.608 pasos adicionales y 500 batallas por control/modelo,
3.000 en total. NORMAL añade 786.432 pasos; HARD añade 3.145.728. CENSUS termina
después de convertir las partidas y no entrena ni evalúa.

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
5. Ejecutar BC solo con ≥1.000 trayectorias y ≥10.000 transiciones elegibles.
   Con menos datos, el candidato parte directamente del champion para PPO.

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

## Interpretar y seleccionar

Champion y candidato juegan la misma agenda espejada contra Random, Max Base
Power y Simple Heuristics. Cada bloque reutilizado identifica modelo, agenda y
runtime. El gate heredado exige mejorar contra Simple Heuristics y no retroceder
en el score global equiponderado. Los empates cuentan medio punto.

`MEJORA_OBSERVADA` es una comparación descriptiva: Showdown mantiene RNG
independiente entre modelos y el gate no demuestra significancia estadística ni
nivel contra jugadores humanos. Al reutilizar el grupo reservado para seleccionar
modelos, pasa a actuar como validación. El informe separa la muestra de equipos
recién reservados; puede ser pequeña o no existir.

Cada ejecución conserva `report.txt`, `report.json`, `comparison.csv`, checkpoints,
chunks y replays sin empaquetarlos. `Refresh/latest_run.txt` y `latest_result.json`
apuntan al último resultado completo. Los checkpoints SB3 usan su formato ZIP
nativo; no se generan bundles ZIP de resultados.

La celda final está desactivada por defecto. Activarla después de revisar un PASS
selecciona explícitamente el candidato como champion del siguiente ciclo, siempre
que el champion de referencia no haya cambiado. Registra padre y ejecución en
`promotion.json`. La instalación en ROG y el uso por Nana son pasos separados.

## Validación de esta implementación

Pruebas locales sin GPU/red sobre separación histórica, variantes de pastes,
filtro de ambas perspectivas, duplicados, refresh tras agotamiento, presupuesto
de páginas, contratos de reanudación, hashes, informes y selección explícita.
Se comprueba también la sintaxis de todas las celdas y los tests existentes de
LIGHT v2/v3, entrenamiento, split, censo extendido y benchmark.

El entrenamiento y las 3.000 batallas de este nuevo ciclo deben ejecutarse en
Colab. Las pruebas locales no constituyen un resultado de calidad del candidato.
