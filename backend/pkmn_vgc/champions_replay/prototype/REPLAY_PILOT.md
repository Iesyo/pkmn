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
Ies revisó visualmente el HTML de este caso y confirmó que se ve bien.

## Diagnóstico cc5298b5ccb1421c · motes japoneses

El job productivo terminó en error: «Faltan especies para identidades
observadas: __champions_actor_p2_0002__» y «La fuente terminó sin una batalla
completa». El ZIP contiene sólo `job.json` y la traza OCR, sin replay archivado.
Team Preview repite seis especies por lado; la pantalla de resultado confirma
«You defeated ゆぐりか!» en frame 322, después del abandono en 318.

Primer pase conservado: 25 candidatos, 19 sucesos, un episodio de PS y siete
avisos abiertos: identidad y PS iniciales de un rival, Mega y movimiento
atribuidos al ocupante erróneo, tres lecturas de debilitamiento sin sujeto
fiable. El HUD de Incineroar dice «わ5びくん» y la narración repetida
«わらびくん»; Ledger sólo relaciona ambas cadenas si el mote con un único
dígito OCR permanece en el mismo HUD dos fotogramas seguidos y el anuncio
completo de debilitamiento se repite. El mismo vínculo permite recuperar el
panel de Intimidate en 207 y asignar los titulares Gengar e Incineroar a
slots distintos. Los PS iniciales de Gengar se confirman en el HUD posterior.

Los OCR de 302 y 303 repiten el debilitamiento de Incineroar; en 302 hay una
`h` intrusa en el mote. Sólo se descartan con PS cero confirmados de
Incineroar, PS positivos confirmados de Gengar y el anuncio idéntico en
300–301 sin acción intermedia. Ledger final: 25 candidatos, 20 sucesos,
16 consistentes, cuatro lecturas duplicadas suprimidas, cero avisos.
El replay tiene un turno, cuatro titulares, Mega Gengar, tres movimientos,
un debilitamiento, Intimidate y los dos equipos completos. El ganador Roku
procede exclusivamente del resultado OCR. Ies confirmó visualmente que este
HTML se ve bien.

## Diagnóstico 472247f7de7e45bf · Ilusión y HUD desplazado

El ZIP archiva una batalla productiva de seis turnos y un replay, que se
conserva sólo como comparación. Ledger hizo un primer pase inmutable:
125 candidatos, 75 sucesos, 19 episodios de PS y dos avisos abiertos.
El detector leyó `0/100` para Excadrill en frame 454 porque el HUD se mueve
hacia la derecha y el OCR sólo conserva `00%`; el nombre se recorta a
`adrill`. En frames 452–453, nombre y `100%` aparecen completos y quietos.
Por eso el supuesto `heal` de 100 % en 497 era el mismo estado, visible antes
del daño real de Hyper Voice en 498–501.

Ledger final conserva el evento candidato 454 como lectura rechazada con
evidencia de desplazamiento, mantiene 100/100 y suprime la repetición de 497.
El daño posterior queda 100/100 → 68/100; hay 18 episodios de PS
confirmados, 72 sucesos consistentes, tres suprimidos y cero avisos abiertos.
La prueba negativa mantiene el aviso cuando falta el nombre recortado o
una de las dos lecturas completas, y un `0%` completo no se descarta.

Al inicio, el mismo actor Zoroark-Hisui aparece con la apariencia de
Excadrill. El puente emite `|switch|` con la apariencia y, al terminar la
Ilusión con PS 1/100 confirmados, emite `|replace|` para el actor real según
el protocolo de Showdown. El Excadrill verdadero entra después como otro
actor. El replay nuevo incluye seis Pokémon por lado, seis turnos, 23
movimientos, seis debilitamientos y el ganador Roku confirmado por OCR.
La comparación con el replay archivado no se usa para imponer transiciones:
el archivado tenía el 0 % falso y un cambio para la revelación.

Las 127 pruebas del autómata y puente pasan (8 diagnósticos opcionales no
disponibles); los JSON de Ledger de seis casos anteriores permanecen
idénticos. Pendiente la revisión visual del HTML por Ies.

Ies confirmó visualmente que el replay HTML de 472247/01 se ve bien.

## Diagnóstico 0f4f21a3e8b04bd5 · Toxtricity-Low-Key

El parser productivo archivó una batalla de siete turnos. Primer pase de
Ledger conservado: 150 candidatos, 86 sucesos (84 consistentes y dos entradas
fantasma tras debilitamiento suprimidas), 26 episodios de PS confirmados y
cero avisos. Los dos supuestos reingresos conservan sus lecturas y la
evidencia del debilitamiento repetido.

El puente se detenía en el daño inicial de Toxtricity-Low-Key: entrada con
100/100 inferidos, primer HUD 94 % en frame 390 y PS finales 81 % en 391.
Hyper Voice comienza en 386, por lo que 94 % es una lectura intermedia del
mismo golpe. El HUD muestra «Toxtricity» y Team Preview contiene una única
forma con esa base, Toxtricity-Low-Key. El puente exige esa unicidad, el
fotograma del HUD entre movimiento y valor final y la continuidad de PS;
emite un solo daño 100 → 81 % y anota el 94 % como evidencia, no como golpe.
Una segunda forma ambigua o un HUD equivocado mantienen el bloqueo.

Replay generado desde Ledger y OCR: ambos equipos de seis, siete turnos,
22 movimientos, siete debilitamientos y ganador Roku corroborado en el
resultado. Las 73 líneas de turnos, entradas, movimientos, PS,
debilitamientos y ganador coinciden con el replay archivado tras quitar
etiquetas de origen auxiliares; el orden de habilidades y terrenos difiere.
Las 128 pruebas del autómata y puente pasan (8 diagnósticos opcionales
omitidos); los replays JSON de 97c1, ad28, cc5298 y 472247 son idénticos.
Pendiente la revisión visual del nuevo HTML por Ies.

Ies confirmó visualmente que el replay HTML de 0f4f/01 se ve bien.

## Diagnóstico 8499d37df0064393 · Tailwind

El parser productivo archivó una batalla de nueve turnos; sus 17 segmentos de
riesgo son avisos orientativos. Primer pase de Ledger conservado: 161
candidatos, 96 sucesos consistentes, 24 episodios de PS confirmados y cero
avisos abiertos. El puente se detenía ante los sucesos `sidestart` y
`sideend`. Ledger observa Tailwind de Pelipper en p2a (suceso 33), su inicio
en p2 (34) y su final en el mismo bando (69), después de varios cambios de
Pokémon. El puente emite `|-sidestart|p2: 3st|move: Tailwind` sólo después
del movimiento inmediato del actor activo, y `|-sideend|p2: 3st|move:
Tailwind` sólo si la condición sigue abierta en ese lado. No asume que el
Pokémon del slot al final sea el que inició Tailwind; rechaza un movimiento,
lado, efecto o cierre contradictorio. Otros tipos de condición lateral
requieren su propia evidencia para determinar el bando afectado.

Replay generado desde Ledger y OCR con ambos equipos de seis, nueve turnos,
26 movimientos, siete debilitamientos y ganador Roku corroborado por OCR.
Las 81 líneas de turnos, cambios, movimientos, PS, debilitamientos, Tailwind
y ganador coinciden exactamente con el replay productivo archivado. Las 130
pruebas de ambos autómatas pasan (8 diagnósticos opcionales omitidos); los
cinco replays JSON anteriores, 97c1, ad28, cc5298, 472247 y 0f4f,
permanecen idénticos. Pendiente la revisión visual del HTML 8499/01 por Ies.

Ies confirmó visualmente que el replay HTML de 8499/01 se ve bien.

## Diagnóstico 44ff1da980a14f5a · ficha de Terrain Pulse

El parser productivo archivó una batalla de cinco turnos con tres tramos de
riesgo orientativos. Primer pase conservado de Ledger: 77 candidatos, 45
sucesos (44 consistentes y un mensaje a revisar), diez episodios de PS
confirmados y un aviso `unclassified_text`. En el frame 539 (04:29.0), el
OCR leyó «Torrain Pulse» en la ficha Move Info, no en la narración de batalla.
En 540–542, el mismo rótulo y posición se leen «Terrain Pulse». Están
visibles «Battle Info», «MOVE TIME» y «Close» y, justo antes, «Move Info».
El anuncio de uso «Dee Dee used Terrain Pulse!» llega en 584–585.

Ledger ahora clasifica como `ui_text` suprimido sólo un fragmento corto en
esa zona del panel, con las marcas de interfaz presentes y dos fotogramas
posteriores con la misma etiqueta legible en la misma posición. Conserva el
OCR y fotogramas como `ui_support`; sin el panel, la posición o las lecturas
repetidas, el aviso queda abierto. Pase final: 45 sucesos (44 consistentes y
uno suprimido), diez episodios de PS y cero avisos. Terrain Pulse cuenta
una sola vez como movimiento al observar el anuncio real.

Replay generado desde Ledger y OCR: equipos completos de seis, cinco turnos,
15 movimientos, un debilitamiento y ganador Roku corroborado en frame 611.
Las 37 líneas de turnos, cambios, movimientos, PS, faint y ganador coinciden
con el replay productivo después de quitar una etiqueta `[from] item:
Leftovers` que no consta en Ledger para la primera cura; no se atribuye ese
origen sin respaldo en el registro. Las 133 pruebas de ambos autómatas pasan
(8 casos opcionales omitidos). Ledger y los replays JSON de 97c1, ad28,
cc5298, 472247, 0f4f y 8499 permanecen idénticos. Pendiente la revisión
visual del HTML 44ff/01 por Ies.

Ies confirmó visualmente que el replay HTML de 44ff/01 se ve bien. El
adjunto `champions-diagnostics-472247f7de7e45bf(1).zip` resultó idéntico
byte por byte al ZIP 472247 previamente archivado (SHA-256
`f49f5ebf937ec67f3938be32fb6eea90d99ade0d01ca42a658cded37c06efcfe`).
Al correr ambos autómatas de nuevo, Ledger produjo el mismo JSON y Markdown
de 75 sucesos, 18 cambios de PS y cero avisos; replay LOG, JSON y HTML
idénticos. Sólo difiere el nombre del ZIP en `report.json`. No es una nueva
partida ni requiere otra revisión visual.
