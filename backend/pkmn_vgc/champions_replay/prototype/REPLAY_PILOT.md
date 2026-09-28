# Champions Ledger → replay: primer piloto

`ledger_replay.py` transforma **una partida cerrada** de `battle-XX.json` en
`.log`, `.json` y `.html` para el visor de Pokémon Showdown. Se ejecuta fuera
del pipeline de COL-102. Los eventos y su orden vienen exclusivamente de
Ledger; el ZIP original aporta los nombres de los jugadores, los equipos,
el formato y el resultado OCR. No lee `output/replay-*.log` ni usa el replay archivado como
autoridad.

## Ejemplo reproducible

Desde el directorio `prototype`:

```bash
python3 ledger_replay.py \
  --ledger /ruta/ledger/f7af8655adb74bc6/battle-01.json \
  --diagnostic /ruta/champions-diagnostics-f7af8655adb74bc6.zip \
  --out /ruta/salida/f7af-ledger-replay
```

Para volver a generar los tres archivos, añadir `--force`. El JSON de salida
incluye `log`, `inputlog`, jugadores, formato, `source_battle_index` y
`ledger_source`: job, correspondencia entre secuencias y líneas, objetivos
desconocidos y fotograma del resultado. En este ejemplo, el ganador se
corrobora con «You defeated Benji!» en el fotograma 605, después del cierre
observado en el 601.

La partida piloto tiene dos turnos, cuatro entradas iniciales, dos cambios,
dos megaevoluciones, seis movimientos, cuatro episodios de daño y dos
debilitamientos. El verificador de COL-102 empareja **10/10 sucesos de
movimiento, megaevolución y debilitamiento** observados y escritos, sin diferencias de orden,
PS cero sin faint ni ocupantes contradictorios. Cuatro pruebas del puente
cubren el piloto real y el rechazo de PS discontinuos, avisos abiertos y
ganador ausente aun cuando el replay archivado diga `|win|Roku`.

## Contrato de evidencia

- Sólo exporta sucesos `consistent`. Omite los `suppressed` y rechaza avisos
  abiertos, sucesos pendientes, tipos sin traductor, saltos de turno,
  identidades incompatibles, HP discontinuos y faint sin cero confirmado.
- Los objetivos sólo se escriben cuando Ledger los asigna o cuando un único
  daño consistente causado por ese movimiento identifica un rival. Taunt
  (secuencia 10) y el último Terrain Pulse (28) conservan el objetivo vacío.
- Los PS de `p1` usan fracciones observadas actual/máximo. Los de `p2`
  permanecen normalizados como porcentaje/100; no se atribuyen PS absolutos
  al rival. No emite decisiones `inputlog` sin evidencia.
- Escribe `|teamsize|`, seis `|poke|` por lado y `|teampreview|` antes de
  `|start|`. El equipo propio viene del contexto configurado o de la lectura
  visual, y el rival de un Team Preview repetido o del contexto si fue
  configurado explícitamente. Si falta un equipo de seis, las lecturas visuales
  empatan o contradicen el contexto, no exporta un replay con equipo parcial.
  `ledger_source.team_preview` conserva los fotogramas de corroboración.
- Exige un único anuncio OCR posterior al fin y dentro de la misma batalla
  que nombre al rival como ganador o perdedor. Si falta, **no fabrica `|win|`**:
  la exportación falla indicando la evidencia faltante.
- En esta primera etapa se traducen entradas, turnos, habilidades, campo,
  clima, megaevoluciones, movimientos, daños, curas, debilitamientos,
  retrocesos (`cant`), pérdida de objeto (`enditem`) y cierre. En reentradas
  sin PS propios, se reutiliza el último valor seguido por el puente y se
  contrasta con `last_confirmed_health` de Ledger cuando existe. En un primer
  avistamiento con máximo inferido, sólo se completa el valor desde la primera
  lectura de HUD confirmada al máximo antes de un cambio de PS. Ambas rutas
  quedan anotadas en `ledger_source.inferred_entry_health`.
  Los demás tipos de Ledger deben recibir traductor y pruebas antes de usar
  otra partida. No hay integración automática con el generador de producción.

El HTML carga el visor oficial mediante su script público; necesita conexión
al abrirse. La comprobación estructural y contra OCR no sustituye una
revisión visual del reproductor.

Ies revisó los dos HTML localmente y confirmó el 28 de septiembre de 2026
que ambos replays quedaron bien. Esta confirmación corresponde a `f7af`/01 y
`79dd`/02; las partidas nuevas requieren su propia revisión visual.

## Segunda partida: 79dd2e3de412450e / 02

```bash
python3 ledger_replay.py \
  --ledger /ruta/ledger/79dd2e3de412450e/battle-02.json \
  --diagnostic /ruta/champions-diagnostics-79dd2e3de412450e.zip \
  --out /ruta/salida/79dd-ledger-replay-battle-02
```

El segundo replay contiene 32 sucesos consistentes y dos turnos. En el
fotograma **1314**, posterior al cierre por rendición, «You lost to Denton!»
confirma `|win|Denton`. El verificador de COL-102 empareja **11/11 sucesos**
(siete movimientos, dos megas, dos debilitamientos), sin diferencias ni fallos
de orden, ocupantes o PS cero.

Una entrada de Politoed lleva 100/100 **inferidos**. Tras comenzar Flare Blitz
en el fotograma 1229, el HUD de Politoed confirma 92 % en 1235 y después 81 %
en 1236. El exportador conserva 100 % como valor inicial inferido, registra
el 92 % como lectura **intermedia de esa misma animación** en
`ledger_source.intermediate_baselines` y escribe el único daño confirmado en
81 %. Sólo permite esa continuidad cuando el actor, el mote, el valor del HUD
y la posición temporal respecto al movimiento están corroborados. Si el 92 %
estuviera antes de la acción, faltara el mote o hubiera avisos abiertos,
rechazaría la exportación. No agrega un daño ficticio de 100 a 92 %.

Seis pruebas del puente, incluidos los dos diagnósticos reales y los casos
negativos del HUD intermedio, pasan. La integración de COL-102 continúa
separada.

## Equipos completos · 28 de septiembre de 2026

Los tres pilotos (`f7af`/01, `79dd`/02 y `92e07d`/01) ahora muestran ambos
equipos completos en la apertura, como el generador productivo. Los doce
`|poke|` se obtienen del contexto del diagnóstico y de las lecturas repetidas
del Team Preview; no se copian del replay archivado. Las 7 pruebas del puente
pasaron con los dos ZIP reales, y en los tres pilotos todas las líneas desde
`|start|` son idénticas a las anteriores. Los HTML nuevos requieren revisión
visual de la pantalla inicial por Ies.

## Diagnóstico 97c1ea4bb7954632 · 28 de septiembre de 2026

Primer pase inmutable de Ledger: una batalla, 261 candidatos, 134 sucesos
(132 consistentes, dos suprimidos), 57 episodios de PS, cero avisos abiertos.
El puente inicial se detuvo ante `cant` y `enditem`; tras traducirlos, exigió
PS en los cambios. Se completaron cinco reentradas desde el último PS conocido
del mismo actor, cotejados con `last_confirmed_health` si estaba presente, y
la primera entrada de Blaziken desde el HUD 156/156 confirmado en frame 623
antes de su primer daño. Dos HUD intermedios durante golpes, frames 360 y 811,
quedan registrados sin fabricar daños adicionales.

Replay resultante: 132 sucesos mapeados, once turnos, siete debilitamientos,
seis especies por equipo y ganador Anshul respaldado por OCR del frame 1843.
El replay de producción archivado sirve sólo para cotejo: los equipos y las
acciones principales coinciden; el piloto omite narraciones auxiliares que
Ledger no convierte en sucesos. Ies confirmó visualmente este HTML.

## Diagnóstico ad28b939f8d84c83 · caso Floette

El parser productivo archivó dos incidencias bloqueantes para la Mega de
Floette-Eternal: un supuesto anuncio sin respaldo de Floette-Eternal y otro
anuncio faltante de «Mega Floette». Ledger mantiene el mismo actor desde el
Team Preview (Floette-Eternal) hasta el anuncio repetido de Mega Floette y
lo convierte en Floette-Mega, sin separar ambas formas como rivales distintos.

El primer pase de Ledger se conserva: 193 candidatos, 107 sucesos, 32
episodios de PS y 12 avisos (cuatro textos no clasificados, cuatro entradas
tardías y cuatro transiciones de PS). Las reentradas tardías reutilizan PS
del mismo actor sólo si la lectura posterior coincide con ellos o, cuando el
HUD ya está animando el golpe, una acción rival y el PS final corroboran la
transición. Los tres mensajes de retirada se vinculan al mismo slot con
retirada y entrada repetidas; la preparación de Mega se vincula a la Mega
confirmada del mismo actor con OCR repetido. Si falta alguna prueba, el aviso
permanece. El segundo pase cierra los 12 avisos con 103 sucesos consistentes
y cuatro mensajes de contexto suprimidos con evidencia.

El puente reconoce «You lost to the Trainer!» cuando el rival se llama
Trainer, valida Floette-Eternal → Floette-Mega con la identidad persistente
de Ledger y recupera la entrada inferida al máximo de Basculegion (219/219)
desde el primer HUD 185/219 leído durante Dazzling Gleam. El 185/219 se
registra como lectura intermedia, nunca como daño extra. Exporta nueve turnos,
24 movimientos, 15 entradas, siete debilitamientos y dos Megas; estas cinco
cuentas coinciden con el replay archivado. El replay nuevo contiene ambos
equipos completos y conserva el ganador corroborado por OCR en frame 1424.
El HTML de este caso requiere revisión visual independiente.
