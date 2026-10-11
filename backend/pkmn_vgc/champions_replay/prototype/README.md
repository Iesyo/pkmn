Warning: truncated output (original token count: 14491)
Total output lines: 900

# Champions Ledger · Autómata temporal de batalla

> **Integración web, 28 sep 2026:** estos dos autómatas son ahora el motor de
> generación de los jobs de vídeo mediante `champions_replay/ledger_pipeline.py`.
> Conservan sus comandos independientes y sus pruebas. La descripción del
> prototipo y los cortes históricos de abajo se mantienen como referencia.
> La ruta vigente y el contenido del ZIP de diagnóstico están documentados en
> `docs/champions-replay.md`.

**Champions Ledger** es un proyecto independiente nacido de los diagnósticos
de COL-102. Lee `ocr.trace.jsonl` y produce un registro intermedio por batalla.
Con `--diagnostic` también lee el equipo de `job.json`; para corroborar una
identidad mediante habilidad usa el catálogo versionado `public/data/showdown-dex.json.gz`.
Si falta ese contexto o catálogo, no aplica esa recuperación. No requiere el vídeo, OCR adicional, Qwen ni
instalar dependencias de Python. No altera el ZIP ni los replays existentes;
se ejecuta fuera del pipeline de producción.

## Ejecutar

```bash
python3 champions_automaton.py \
  --diagnostic champions-diagnostics-10a7fba6fda04585.zip \
  --out resultados
```

También acepta `--trace ruta/ocr.trace.jsonl`. Genera `report.json` y, por
cada batalla, `battle-XX.json` (datos auditables) y `battle-XX.md` (cronología
para una persona). `battle_index` siempre es el índice de la traza y del
`source_battle_index` del replay; no se deduce a partir del número de archivo.

## Máquina de estados de este primer corte

1. Toma los eventos **candidatos** que ya están en la traza y conserva el OCR
   crudo cercano como evidencia. Un candidato previo no se trata como verdad.
2. Mantiene turno, cuatro slots, identidad persistente de cada actor, PS,
   estado, objeto perdido, terreno activo y última acción. Resuelve cada
   identificador provisional por el mote mostrado en el HUD durante **esa
   aparición**. Un ID reutilizado después no cambia a un actor anterior.
   El mote de un movimiento también corrige su slot si aparece en un único
   HUD del mismo bando.
3. Un anuncio `sent out`/`Go!` fija el momento lógico de una entrada que el
   HUD confirma más tarde. La entrada queda antes de sus habilidades y de los
   movimientos posteriores; se conservan ambos tiempos. Los cuatro leads se
   representan en orden estable `p1a,p1b,p2a,p2b`.
4. Consolida lecturas sucesivas de PS durante una animación en **un episodio**
   y deja todos los valores intermedios en `observations`. Agrupa el mismo
   aviso de movimiento leído en varios frames. Los mensajes de quemadura,
   retroceso y recuperación se conservan con actor y tiempo para asociarlos
   después de consolidar los episodios. Una oscilación de PS que vuelve al valor previo
   antes de actuar queda en `review`, sin producir daño y cura inventados.
   La lectura candidata pasa por un estado **provisional**: se buscan el
   valor completo y su posición en el HUD del slot durante el fotograma y los
   dos muestreados siguientes. Se aceptan números y `%` separados pero
   contiguos. Los PS propios exigen una fracción literal `actual/máximo`,
   con máximo positivo y numerador entre cero y ese máximo. No se repara
   un `/` omitido o sustituido: esa lectura se descarta sin cambiar el estado
   confirmado ni cortar una animación. Un número solo, aunque se repita,
   no confirma un porcentaje.
   Una lectura sin apoyo queda en `hp_unconfirmed`: no confirma esos PS y
   obliga a revisar la transición siguiente. Si el PS anterior todavía se
   ve confirmado en **ese mismo HUD**, el valor candidato queda como
   `hp_rejected_reading` suprimido y no invalida el valor estable. Las
   entradas sin PS confirmados conservan el candidato en `observations`.
   Si es el **primer avistamiento** de ese actor y la lectura llega tras
   comenzar una acción (o falta), asume PS iniciales al máximo con
   `hp_state: inferred`. En p2 esto es `100/100`; en p1 deduce el máximo
   del primer HUD completo. Una lectura anterior a la acción, completa y
   atribuida al mote correcto tiene prioridad sobre la inferencia.
   También puede tomar el último PS completo del HUD **justo antes de que
   empiece a bajar o subir la barra**, aunque el aviso del movimiento ya esté
   en pantalla. Exige mote del ocupante, posición correcta y un solo valor
   previo en la ventana de tres fotogramas; conserva esa prueba en
   `hp_baseline` del evento.
5. Si dos números OCR se contradicen en el mismo HUD, conserva ambos como
   evidencia. La lectura aislada contradicha queda en `hp_ocr_conflict`, sin
   modificar los PS ni generar daño/cura. Cuando el número de mayor confianza
   coincide con los PS previos y el HUD completo los repite en al menos dos
   fotogramas inmediatamente anteriores, el porcentaje recortado queda como
   `hp_rejected_reading` suprimido con esa evidencia. No mezcla números de
   HUD separados. Si el detector crea una pérdida y un rebote inmediatos
   antes de la primera acción, también exige PS completos estables después
   del fragmento antes de descartar ambos; sin esa prueba mantiene
   `hp_oscillation` para revisión. Si una cura de ~1/16 bajo Grassy Terrain
   termina en PS confirmados y el mensaje posterior nombra al mismo actor,
   un porcentaje OCR recortado durante la subida se descarta por separado:
   conserva los PS previos y registra una sola curación con causa corroborada.
   Un `0%` seguido de un rebote positivo durante el debilitamiento se
   conserva en `hp_zero_rebound`, sin reanimar al actor. El valor final puede
   estar confirmado por OCR y **seguir en revisión** si el sentido del cambio
   contradice el estado previo o no se conoce el PS anterior. Una observación
   confirmada puede fijar el valor actual aunque se desconozca el cambio que
   lo produjo; una contradicción deja los PS actuales como desconocidos hasta
   una observación posterior confirmada.
6. Un Pokémon debilitado no vuelve a entrar porque su imagen siga en el HUD.
   Se detectan aplicaciones de estado repetidas, saltos de turno y lecturas de
   PS de otro ocupante y megaevoluciones atribuidas a otra especie. Las
   identidades aún anónimas se señalan al terminar la batalla. Cuando la
   primera confirmación del HUD sucede después de que comenzó un movimiento,
   conserva ese número como lectura transitoria y aplica la hipótesis del
   primer avistamiento. Si ya conocía al actor, mantiene sus PS previos o
   deja la entrada pendiente de revisión. Si la pantalla anuncia que se rompió
   la Ilusión de Zoroark, el supuesto cambio de su apariencia es
   `illusion_reveal`: conserva actor, PS, objeto y movimientos previos, y
   registra la especie mostrada por separado de la identidad real. La entrada
   posterior del Pokémon imitado crea o recupera **otro actor**.
   Un candidato Mega suprimido por slot incorrecto puede resolverse después
   si existe una única Mega aceptada de la misma especie, forma y piedra en
   otro slot, en el mismo turno y a ≤3 s. Exige texto de especie/mote, bando
   y piedra concordantes en dos fotogramas distintos, con confianza OCR
   ≥0,95, y ninguna acción, entrada, turno o faint intermedio. Guarda el
   candidato original, `resolution` y la incidencia en `resolved_issues`;
   el informe separa `resolved_issue_codes` de las incidencias pendientes.
   También resuelve una entrada suprimida alrededor de un faint si ambos
   pertenecen al mismo actor, slot y turno, a ≤3 s, y no media otra acción,
   entrada o cambio de turno. Exige daño aceptado a cero, confirmación del
   cero en ese HUD al debilitarse o en los dos fotogramas previos, y anuncio
   de faint del mismo actor/bando repetido en dos frames (confianza ≥0,95).
   Un anuncio de entrada en la ventana o PS positivos en el candidato
   mantienen la revisión. Sirve tanto para reentradas tras faint como para
   duplicados inmediatamente anteriores, incluso en el mismo frame.
   Para una identidad provisional creada al regresar el HUD, exige nombre y
   PS completos en el mismo slot antes del candidato (hasta 8 s) y en dos
   frames posteriores (hasta 1,5 s), con confianza ≥0,95. En esos dos frames
   la resolución local del ID y el alias leído deben identificar al ocupante.
   Los PS previos deben estar confirmados y ser positivos. Una lectura
   contradictoria, acción, entrada, salida, faint o cambio de turno anterior
   al candidato impide cerrar el aviso; el paso al menú del siguiente turno
   sí puede corroborarlo. La resolución conserva el candidato suprimido y
   la evidencia; no modifica entradas, actores ni PS.
7. Ejecuta una **pasada retrospectiva** para los mensajes de quemadura,
   retroceso y recuperación. Busca episodios confirmados del mismo actor,
   slot, turno y sentido del cambio, a ambos lados del texto, hasta 3 s desde
   el intervalo observado de la animación. No atraviesa otra acción, fin de
   batalla o una entrada en ese slot. Si dos episodios quedan a distancias
   similares (margen de 0,5 s), o efectos distintos compiten por el mismo
   episodio, conserva el mensaje pendiente y genera una incidencia.
   Resuelve motes por bando y permite narración tardía tras un debilitamiento
   cuando el último ocupante sigue identificado y su slot no fue reemplazado.
   `narration_links` guarda todos los mensajes, candidatos y estados
   `linked`, `ambiguous` o `unmatched`. Los enlaces aceptados agregan
   `causal_evidence` al episodio: texto, frame, tiempo, efecto, confianza OCR
   y desfase respecto al comienzo del cambio. El paso no modifica valores de
   PS ni el orden de los eventos. Una frase genérica de recuperación no
   demuestra por sí sola su origen; Grassy Terrain conserva su grado de
   corroboración previo. El informe cuenta los enlaces en `hp_narration`.
   Las variantes de sujeto OCR se evalúan por continuidad incluso cuando
   tienen confianza alta. Dos lecturas literales del mismo bando y efecto,
   un único episodio de PS confirmado y muestras continuas permiten
   descartar una variante ortográfica pequeña del mismo anuncio. No se
   crea un alias global ni se cambian PS, actores o acciones. Nombres
   conocidos distintos, otro efecto, otra acción, cambio de ocupante o
   huecos conservan el aviso; el texto descartado mantiene su evidencia.
   Si completar un punto final modifica los PS anteriores de la siguiente
   lectura, se vuelve a comprobar la transición: PS iguales se conservan
   como observación suprimida del mismo punto final, con la transición
   original auditada. Una segunda curación o daño real se conserva.
8. Compara la secuencia de eventos principales y episodios de PS con los
   replays archivados de los tres jobs. Los desacuerdos se informan; un replay
   archivado puede contener una entrada espuria por Ilusión o por un ID
   provisional reutilizado.

`consistent` significa **sin contradicción estructural detectada**. No
significa que el evento esté confirmado visualmente. `review` requiere mirar
el fotograma o investigar una causa; `suppressed` conserva la lectura en el
JSON pero la excluye de la cronología propuesta. El prototipo **no** exporta
todavía un replay Showdown: sería prematuro mientras haya discrepancias.
En los eventos pertinentes, `hp_state` distingue `confirmed`, `inferred`,
`unconfirmed`, `unknown` y `rejected`.
`hp_support` guarda la evidencia OCR y la razón de la confirmación o rechazo;
el estado del actor distingue PS confirmados, PS iniciales inferidos y PS
desconocidos. `report.json` cuenta por separado inferencias de entradas y de
transiciones, episodios, observaciones confirmadas, lecturas sin confirmar y
transiciones que requieren revisión.

## Primer ZIP · 10a7fba6fda04585 (corte inicial)

| Partida | Candidatos → sucesos | Orden principal frente al replay | PS coincidentes | Hallazgo |
| --- | ---: | ---: | ---: | --- |
| 1 | 206 → 109 | 62/62, orden exacto | 35/37 | Lectura parcial `3%` frente a `18` en el mismo HUD; el HUD repite Rillaboom tras faint; 93/207 en traza y 94/207 en replay. |
| 2 | 184 → 100 | 56/56, orden exacto | 33/34 | Kingambit está a 0 % en su HUD; luego una lectura OCR aislada confunde el 88 % de Rillaboom con 0 % y el replay archivado escribe una cura. |
| 3 | 94 → 51 | 32/32, orden exacto | 15/15 | Sin discrepancias detectadas por este corte. |
| 4 | 190 → 107 | 68/68, orden exacto | 32/32 | Un mensaje queda sin acción causal identificable y se informa para revisión. |
| 5 | 174 → 104 | 57/57, orden exacto | 30/30 | Pelipper y Rillaboom se anuncian antes de que el HUD confirme su PS; éste ya cambió cuando se leyó. |

En la partida 2, la captura a los **23:02** muestra a **Kingambit a 0 %** en el
HUD derecho. La traza atribuye correctamente ese PS a `p2b: Kingambit` (frames
2766–2773; posición horizontal del `0%` ≈ 0.92). La captura a los **23:18**
muestra al Rillaboom rival con **88 %** en otro HUD. En el frame 2796
(23:17.5), el OCR leyó a la vez `88` (confianza 0.99996) y `0%` (0.78096)
en la zona del PS de Rillaboom (posición ≈ 0.70–0.75); en el frame 2797
(23:18) leyó de nuevo `88%`, sin acción intermedia. La lectura `0%` de 2796
parece un recorte fallido del número de Rillaboom: está en otra posición que
el `0%` real de Kingambit. El replay archivado añadió una curación
`|-heal|p2a: Rillaboom|88/100` al inicio del turno 8. El autómata conserva
la oscilación y el conflicto OCR para revisión, y no emite daño ni curación a
partir de ella. Las capturas no incluyen el fotograma exacto 2796. La
partida 1 necesita resolver **frames 1233–1234** (`94/207` y `93/207`). En la
partida 5, las entradas de Pelipper y Rillaboom anunciadas antes de los
frames **5550 y 5826** usan PS iniciales al máximo **inferidos**; el PS leído
durante la animación no demuestra por sí mismo el valor con el que entraron.

## Segundo ZIP · 90403f16712d4d41 (corte inicial)

El ZIP contiene la salida actual y dos archivos de reanálisis anteriores.
La primera versión produjo sólo tres replays: una identidad rival en la
tercera batalla (`__champions_actor_p2_0002__`) seguía sin especie. El
autómata ahora emite `unresolved_identity` para esa traza; no la trata como
lista para exportar. En esa misma versión, Archaludon osciló `0 → 9 → 0 → 9 →
0 %` durante su debilitamiento; ambos `9 %` quedan como incidencias
`hp_zero_rebound` y no como curaciones.

| Partida | Candidatos → sucesos | Orden principal frente al replay actual | PS alineados | Hallazgo |
| --- | ---: | ---: | ---: | --- |
| 1 | 169 → 91 | 55/55 | 32/32 | Golisopod conserva la parálisis observada; no hay cura de estado. |
| 2 | 164 → 92 | 47/47 | 32/34 | Un falso Mega de Delphox apuntaba al slot de Indeedee; `3%` dos veces y `1%` una vez compiten con `28`, `28` y `44` en sus respectivos HUD. El autómata marca los tres sin cambiar PS. |
| 3 | 150 → 84 | 44/46, diferencia intencional por Ilusión | 31/31 | El Kingambit inicial es Zoroark-Hisui disfrazado; rompe la Ilusión a 1 %, y después entra un Kingambit real. Justo antes de bajar a 85 %, su HUD muestra 93 %. |
| 4 | 114 → 67 | 42/42 | 17/17 | Indeedee-F mantiene la parálisis. Un texto `can't use` sin acción identificable queda en revisión. |

Los PS `1%` de Sneasler y `3%` de Delphox parecen recortes OCR de menor confianza,
no evidencia suficiente para crear daño y posterior curación. El `44` leído
junto al `1%` de Sneasler tampoco prueba por sí solo cuál fue su PS exacto en
ese instante. El replay archivado de la partida 2 contiene dos episodios de
PS más que el registro propuesto; no se toman como verdad visual.

En la **partida 3**, el rival aparece como Kingambit al principio, pero usa
Hyper Voice y Bitter Malice. Su HUD baja a **1 %** (frame 2875), consume la
Banda Focus (2882) y la pantalla dice que la Ilusión de Zoroark terminó
(2893–2896). No salió un Pokémon nuevo en ese momento: todos esos sucesos
pertenecen al **mismo Zoroark-Hisui**. Después se retira Zoroark (texto en
2982) y se anuncia al **Kingambit real** (2988). Su primer PS observado es
85 % después de Grassy Glide (3001); su entrada se anota como 100 % inferido,
pero
el HUD muestra **93 % en el frame 3000**, antes del impacto. Ese daño se
registra como 93 → 85 %, sin el falso salto de 1 % a 85 %. Kingambit cae en
3152; Zoroark vuelve en 3193 con su último
PS confirmado de 1 % y cae en 3282. Las dos diferencias de eventos con el
replay archivado corresponden a la entrada inicial bajo la identidad falsa y
al falso cambio que el replay inventó al romperse la Ilusión.

La coincidencia de eventos principales **no prueba fidelidad**: el autómata
parte de candidatos del mismo detector que creó los replays archivados. Faltan
un extractor independiente de observaciones puras, atribución causal completa
de PS y una revisión visual de las discrepancias restantes.
Este corte establece la frontera entre observación, evento consolidado y
emisión de Showdown para continuar sin reprocesar el vídeo.

En las **nueve partidas de los primeros dos ZIP**, los 259 episodios de PS propuestos y no
suprimidos conservan evidencia OCR verificable en su HUD. Ocho secuencias
principales coinciden exactamente con sus replays archivados; en la novena,
el autómata corrige las dos atribuciones causadas por Ilusión. Ninguno de esos 259
episodios necesitó pasar por `hp_unconfirmed`; se detectaron y conservaron en revisión
los conflictos y transiciones dudosas descritos arriba. Este conjunto prueba
que la puerta no descartó esas lecturas válidas, **no** que pueda evitar toda
lectura parcial en futuros vídeos: un OCR erróneo, completo y persistente puede
requerir una observación adicional o revisión visual.

## Tercer ZIP · 331e6e783c3e45a4 (corte inicial)

El diagnóstico nuevo añade tres partidas. Sus avisos bajan de **13 a 0**:
varias entradas tienen PS completos en pantalla antes de la primera acción,
aunque el detector no los añadió al candidato. La primera aparición de Inwood
aplica la hipótesis de PS completos iniciales cuando su primer HUD aparece
durante el daño.

| Partida | Candidatos → sucesos | Avisos antes → ahora | Orden frente al replay | Episodios de PS |
| --- | ---: | ---: | ---: | ---: |
| 1 | 154 → 83 | 2 → 0 | 50/50 | 30/30 |
| 2 | 102 → 58 | 2 → 0 | 37/37 | 18/18 |
| 3 | 139 → 91 | 9 → 0 | 55/57 | 22/22 |

En la tercera partida, el identificador provisional `...p2_0003` designó
primero a **Indeedee-F (Inwood) en p2b** y después a **Salamence (Farmingdale)
en p2a**. Ambos aparecen a la vez en el frame 2999: Farmingdale a 100 % en
el HUD izquierdo e Inwood a 6 % en el derecho. La segunda detección de la
entrada de Salamence confirma los PS de su entrada anunciada en 2991. El
Hyper Voice de Farmingdale en 3057 pertenece a `p2a`; el daño que derrota a
Inwood en 3161 parte de **6 %**, no de los 65 % del HUD vecino. Cinco
lecturas candidatas confundidas entre ambos HUD siguen auditables en JSON
como `hp_rejected_reading`, junto con los PS persistentes de Inwood. El
replay archivado nombró Indeedee-F a la primera entrada de Salamence y luego
duplicó su entrada; de ahí las dos diferencias de eventos principales.

En la primera partida, el `207/207` de Rillaboom en el frame 584 está en su
HUD **antes** de que el golpe de Iron Head reduzca sus PS en 585–586; el daño
queda establecido como 207 → 117 y ya no genera aviso. Lo mismo ocurre con
Gardevoir en la tercera: `168/171` en 2525 antes de bajar a `113/171` en 2527.
En el frame 2735, Inwood muestra 76 % mientras baja la barra: ese número queda
como observación transitoria, con entrada `100/100` inferida. El frame 2736
confirma 54 % y el episodio queda `100/100 → 54/100` con
`hp_baseline.state: inferred`, no como PS iniciales confirmados en pantalla.
En las **doce partidas**, los avisos bajan de **61 a 15** (primer ZIP: 28 → 8;
segundo: 20 → 7; tercero: 13 → 0). Los **329 episodios de PS propuestos** tienen respaldo OCR
del valor final en su HUD; algunos PS iniciales se infieren. Los avisos pendientes siguen visibles; estos datos aún no prueban
que el replay final reproduzca fielmente el vídeo.

## Refinamiento de episodios y textos de menú · 27 de septiembre

- En el job `10a7fba6fda04585`, batalla 1, el aviso de quemadura aparece en
  frame 369 y los PS de Indeedee-F bajan `18 → 15 → 12 %` en frames 370–371.
  El detector llamó `heal` a 15 % con una etiqueta de Rocky Helmet; Ledger
  conserva ambas lecturas y registra **un daño 18 → 12 % por quemadura**. La
  etiqueta errónea del candidato no determina la dirección ni la causa. La
  narración de quemadura ya no se atribuye al daño de Blaziken en el otro slot.
- Los mensajes `has no energy left to battle` y `can't use its sealed...`
  vistos durante `Battle Info` o `MOVE TIME` quedan en JSON como `ui_text`
  suprimido: son avisos del menú, no sucesos de la batalla. Fuera del menú
  siguen necesitando interpretación normal.
- Recuento actualizado en las mismas trazas: **11 incidencias** pendientes
  (primer ZIP: 5; segundo: 6; tercero: 0), frente a 15 antes de estas dos
  correcciones. Hay **328 episodios de PS** propuestos. Los sucesos
  principales mantienen las mismas alineaciones con los replays archivados.
  Las incidencias restantes incluyen candidatos suprimidos para auditoría;
  ninguna reducción equivale a validación visual del vídeo.

## Confirmación visual y fragmentos OCR · 27 de septiembre

- La captura aportada del job `10a7fba6fda04585`, partida 1, frame 358
  (02:58.5), muestra **18 %** en el HUD de Indeedee-F. El `3%` de la traza
  es un recorte superpuesto al `18` de mayor confianza; los fotogramas
  anteriores repiten `18%`. Se conserva como `hp_rejected_reading` suprimido,
  con las observaciones originales y sin transición de PS.
- La misma regla de evidencia repetida suprime los `3%` de Delphox en el
  segundo ZIP, partida 2, frames 1694 y 1766: el `28` de ma…3491 tokens truncated…o avisos pendientes y ocho
incidencias resueltas auditables**. Garchomp ahora conserva cero PS confirmado
al debilitarse; antes su salud final quedaba desconocida.

La coincidencia de secuencias principales y PS con los dos replays archivados
del job nuevo es completa, pero éstos comparten el mismo origen OCR. Sólo
el anuncio de Golisopite recibió confirmación visual nueva en este corte.
El lote 508 ya forma parte del corpus de regresión: se necesitan otros vídeos
sin ajustes para seguir evaluando generalización. Sigue pendiente integrar
la salida en Showdown y en producción.

## Quinto ZIP · 9fd1afffbf8340df · identidad durante la entrada del HUD

El primer corte sin ajustes produjo **14 avisos (12/2)**. Las capturas de Ies
muestran el HUD aún ausente a 01:21.50 y claramente visible a 01:22.50:
Tonatiuh/Blaziken con 156/156 PS y Tomoe/Kingambit con 177/177. La captura
temprana no permite inspeccionar los nombres en movimiento; en la traza,
el frame 164 contiene Tomoe temporalmente dentro de la zona de p1a y Tonatiuh
fuera de ella. Elegir el primer alias cercano fijaba dos Kingambit propios.

Cuando una entrada provisional tiene nombres de especies diferentes en su
ventana local, la identidad espera corroboración: dos frames consecutivos,
nombre único en ese slot del bando, confianza ≥0,95 y posición estable
(variación ≤0,01 de ancho/alto normalizados). Se mira desde la entrada hasta
tres frames más, con límite de 1,5 s y sin huecos de más de 1 s. Acciones,
Mega, faint, fin de batalla, reemplazo en el slot y anuncios nuevos de
acción/entrada/salida detienen la búsqueda. El paso al menú de turno puede
corroborar el HUD. Una contradicción posterior invalida la prueba anterior.
Sin prueba suficiente, la entrada queda suprimida con
`entry_identity_unconfirmed`; una resolución global del ID no fuerza el
ocupante. `identity_support` conserva ID original, nombres candidatos,
coordenadas, confianza y frames de confirmación, sin desplazar la entrada.

Los frames **165–166** confirman a Blaziken. Su Mega en **239**, daños
**156 → 35 → 0 PS** y retroceso quedan vinculados al actor correcto.
El lote baja de **14 a 6 avisos (4/2)** con este único cambio; los cuatro
restantes de la primera partida corresponden a entradas tardías/atribución
de acciones. En la segunda siguen el faint duplicado y el panel informativo.
No se consideran resueltos por corregir el HUD inicial. El lote conserva
27 episodios de PS confirmados y seis de siete mensajes de PS enlazados.

**66/66 pruebas aprobadas con cinco ZIP (16 partidas)**. Los JSON completos
de las 14 partidas previas y de la segunda del lote nuevo son idénticos a
los del corte previo. El primer corte del ZIP nuevo queda archivado separado.
El prototipo sigue fuera de producción y no emite replays Showdown.

## Entradas tardías con títulos · Revenant/Basculegion

Las cuatro capturas nuevas confirman **Go! Revenant the Paldea Champion!**
a 03:59–03:59.50, seguido del HUD **Revenant 219/219**, junto a Tomoe 78/177,
a 04:07–04:08. La entrada candidata de Basculegion llegaba recién en el
frame 672 (05:35.50), después de su Aqua Jet, fuera de los 80 frames del
anclaje habitual.

El traductor y Ledger ahora comparten la lista y función exacta de títulos
en `champions_replay/pokemon_names.py`. El traductor conserva su importación
`_strip_pokemon_title`; la extracción no modifica sus reglas. Se quitan
únicamente sufijos conocidos, preservando motes reales, títulos con `and`
y anuncios dobles. Ledger sigue funcionando como CLI sin dependencias OCR.

Para el candidato tardío se añade una corroboración conservadora: anuncio
repetido en al menos dos frames (confianza ≥0,95), mote exacto después de
retirar el título, slot previamente liberado por faint, y dos HUD consecutivos
con nombre único y posición estable (≥0,95) y los mismos PS completos (≥0,90).
El HUD debe llegar antes de otra acción, en ≤80 frames y ≤40 s. La continuidad
hasta el candidato exige ausencia de reemplazo, faint, retirada, cierre,
identidad contradictoria, huecos >1 s o cambios de PS. Si los PS intermedios
faltan, se conserva el caso pendiente para reconstruir esos episodios.

`entry_reconstruction` conserva el candidato original completo y las pruebas.
La entrada lógica de Revenant queda en **479**, confirmada por **495–496**
con **219/219 PS**; el frame observado del candidato sigue siendo **672**.
La acción Aqua Jet de **609** queda asociada al actor ya presente. El lote
baja de **6 a 5 avisos (3/2)**; conserva 27 episodios de PS confirmados y
seis de siete mensajes enlazados. Gori sigue pendiente: su primer HUD muestra
207/207, mientras el candidato tardío marca 35/207 y faltan episodios
intermedios. También siguen los dos avisos de la segunda partida.

**69/69 pruebas Ledger aprobadas, cinco ZIP y 16 partidas**. Los eventos,
orden, actores, PS y avisos de las 14 partidas anteriores se conservan.
Doce de esos JSON son idénticos; en dos sólo se añade evidencia de anclaje
para cinco entradas con títulos ya presentes en el mismo frame del candidato.
La segunda partida del nuevo lote permanece idéntica. La suite del traductor
ejecuta 150 pruebas: **149 aprobadas y una omitida por falta de RapidOCR**,
sin fallos. Las capturas y el primer corte del lote permanecen archivados.

## Aparición identificada tarde con acciones y PS omitidos · Gori/Rillaboom

La curación de campo ya estaba contemplada. En este caso no llegaba a esa
regla porque el detector identificaba a Gori recién en el frame 899 y dejaba
sin candidatos su daño y curación anteriores. Fake Out se entregaba en 882,
con su reloj original de 703 pero atribuido a p1a; Grassy Glide también
quedaba en p1a. La mejora amplía la corroboración de entradas tardías a una
aparición con cambios de PS verificables, sin condiciones por especie o job.

Se mantienen anuncio repetido, slot vaciado, primer HUD estable y continuidad
de identidad. Cada cambio omitido exige PS completos y mote único del mismo
HUD con confianza ≥0,95, denominador constante y ausencia de rebote desde
cero. Las animaciones se agrupan en ≤1,5 s y deben terminar en una lectura
repetida dentro de ≤5 s, sin cruzar otra acción. Si la entrada candidata
llega durante un impacto, el extremo sólo puede confirmarse continuando por
candidatos de PS reales del mismo slot y dirección. Los huecos, valores
contradictorios o cambios sin confirmación conservan la incidencia.

Los movimientos retenidos usan su reloj y frame originales más dos textos
concordantes del mismo actor, lado y movimiento dentro de la aparición
corroborada. `action_reconstruction` conserva íntegro el candidato original,
el frame de entrega tardía y la narración. Las observaciones recuperadas llevan
`hp_reconstruction` y pasan a la agrupación y asociación de curaciones
existentes. La traza de entrada permanece intacta.

Resultado en 9fd1, partida 1: entrada de Gori en **650**, HUD **207/207**
confirmado en **672–673**, Fake Out en **703/p1b/turno 4**, daño
**207→31**, curación **31→43** enlazada al mensaje de **776**, Grassy Glide
en **882/p1b/turno 5** y daño final **43→0**. Las lecturas de animación se
conservan, incluida **35/207 en 899**. La captura de **06:02.50** muestra
**35/207** durante el movimiento de la barra; la traza estabiliza **31/207**
en 726–739. Esa captura no se usa como confirmación visual del 31.

**73/73 pruebas Ledger, sin omisiones, cinco ZIP y 16 partidas**. Los 15 JSON
ajenos a esta partida son idénticos al corte anterior. Los avisos del lote
bajan de **5 a 2 (0/2)**, con **29 episodios de PS confirmados** y los
**7/7 mensajes de PS enlazados**. La partida corregida coincide en los
**17/17 episodios de PS** del replay archivado; alinea **37/38 eventos
principales** porque sigue sin un candidato de habilidad Grassy Surge.
Ausencia de avisos no significa extracción completa de todas las habilidades.
Siguen pendientes el faint duplicado de 1438 y el texto de interfaz de 1587
en la segunda partida. El prototipo continúa fuera de producción.

## Texto transitorio antes de aplicar un debilitamiento

El estado retrospectivo de texto transitorio ya existía, pero sólo recibía
mensajes sin clasificar. Una lectura incompleta que el detector convertía
directamente en `faint` se aplicaba antes de comprobar su sujeto. En 9fd1,
partida 2, `omoe fainted!` (1437) tenía confianza OCR **0,98236**, mayor que
`Tomoe fainted!` (1438, **0,97972**). Ese valor no certifica que el nombre
esté completo ni que corresponda al actor del slot.

Cuando la traza aporta narración de debilitamiento, se comprueba el sujeto
contra especie/motes del actor y el lado, además de confianza ≥0,95. Si
ninguna lectura cumple, el candidato queda en `faint_text_unconfirmed`:
no vacía el slot, no cambia los PS ni marca al actor como debilitado. El
texto, la puntuación y el candidato original permanecen en `text_support`.
Las fuentes que no aportan esa narración mantienen su tratamiento previo;
esta comprobación cubre lecturas explícitas de sujeto dudoso o poca confianza.

El mismo reconciliador de texto transitorio puede descartar esa lectura
cuando existe un único faint posterior aceptado del mismo actor/slot/turno,
PS cero confirmados y al menos dos anuncios completos concordantes dentro
de la ventana existente de tres segundos, sin cruzar acciones, cambios,
turnos o huecos. Un nombre conocido de otro actor o un lado distinto no se
reinterpreta por parecido. Sin corroboración, la incidencia permanece.

La captura de **11:58.00** muestra todavía «It's not very effective on
Tomoe.» y no confirma visualmente el OCR parcial de 1437. La de
**11:58.50** muestra «Tomoe fainted!» completo y 0/177. El candidato 1437
queda provisional y luego descartado con evidencia; **1438** registra el
único debilitamiento, corroborado por el texto repetido en **1439–1440**.

**76/76 pruebas Ledger sin omisiones**, cinco ZIP y 16 partidas. Los otros
15 JSON son idénticos al corte anterior. Avisos: **2→1 (0/1)** en 9fd1;
siguen 29 episodios de PS confirmados y 7/7 mensajes asociados. La partida
2 mantiene secuencias exactas de 33/33 eventos principales y 12/12 episodios
de PS. Sólo queda el texto del panel informativo de Psychic Terrain en
1587 (13:13), pendiente de revisión. Prototipo fuera de producción.

## Panel de consulta Active Statuses & Effects

La captura de 13:13.00 del job 9fd1, partida 2, confirma que el texto
«are immune to priority moves.» procede de la descripción de Psychic
Terrain dentro del panel de consulta. Ese panel cubre casi todo el campo:
sus cifras, nombres y descripciones no son observaciones de una acción.

Antes de cualquier pasada, Ledger reconoce el encabezado completo
**Active Statuses & Effects** con confianza ≥0,90. Tolera mayúsculas,
espacios y `and` en lugar de `&`. En cada frame reconocido excluye OCR,
candidatos, alias e identidades de la vista de trabajo, conservando sus
tiempos. Así el panel no puede producir eventos ni confirmar PS, sujetos
o narraciones de otros eventos en la pasada retrospectiva. La traza
original no cambia y la lectura normal continúa en frames sin ese panel.
Un rótulo genérico como Battle Info, el nombre del terreno, una descripción
o un encabezado incompleto/débil no bastan para excluir un frame.

`ignored_ui_frames` conserva frame, tiempo, motivo, encabezado, OCR y
detecciones originales. El total de candidatos sigue contando la traza
original; los candidatos del panel quedan en esa auditoría, fuera de los
sucesos de batalla. El informe Markdown indica los frames excluidos.

Se excluyen **1587–1588** de 9fd1, eliminando el último aviso. La misma
regla reconoce **3093–3096 de 331, partida 3**, que no tenían candidatos:
allí sólo se añade evidencia de exclusión y los sucesos siguen idénticos.

**79/79 pruebas Ledger aprobadas, sin omisiones; cinco ZIP, 16 partidas,
cero avisos pendientes**. Catorce JSON son idénticos al corte anterior;
el de 331 partida 3 sólo añade auditoría de interfaz. En 9fd1 partida 2
se retira el candidato informativo del registro de batalla y se conserva
en la auditoría: siguen 33/33 eventos principales y 12/12 episodios de PS
en secuencia exacta. El lote mantiene 29 episodios confirmados y 7/7
mensajes de PS asociados. No se modifica el traductor de producción.

## Zonas de reloj y separador omitido

Las cinco capturas del lote f7af confirman Dee Dee con **177/177** en
01:31.50, 01:32.50 y 01:38.50, y Raichu con **100 %** en 13:20.50 y
15:06.50. La traza convierte `05:18 0` en `5/180` (1603) y `04:330`
en `4/330` (1815): esas líneas comienzan en la zona del reloj rival y
se extienden sobre los iconos del equipo, debajo de los PS.

Ledger clasifica las cifras de ambos relojes por sus coordenadas
normalizadas antes de todas las pasadas. Usa el origen de la caja OCR,
no su centro, porque puede incluir iconos a la derecha. Los números de
esas zonas no sirven para confirmar PS, entradas o reconstrucciones.
Se conserva la traza original; no se descarta el frame ni su HUD real.

Cuando un candidato archivado de daño/curación coincide exactamente con
una cifra del reloj del mismo frame y no tiene lectura compatible en el
HUD de su slot, se registra como `hp_rejected_reading`. Conserva texto,
confianza, coordenadas, zona y candidato original en `hp_support`, sin
cambiar PS ni cortar una animación en curso. Una lectura compatible en
el HUD, incluso débil, impide atribuirla sólo al reloj: se aplica la
validación normal o queda pendiente. No se descarta por denominador
inusual, presencia de Communicating ni mera proximidad a un reloj.
El HUD rival porcentual sólo puede confirmar valores con denominador 100.

**Histórico, sustituido por la validación literal descrita abajo.**
En este corte, para PS propios como `177177`, se amplió la reparación del
separador: numerador igual al máximo, máximo de al menos dos cifras,
dos lecturas consecutivas ≥0,90, posición estable, mote único e idéntico
y sin cruzar acciones, cambios, turnos o huecos. No se adivinan fracciones
parciales ambiguas. Dee Dee queda confirmado en su entrada 184 con
evidencia de 184–185; no necesita esperar hasta la lectura con `/` en 198.

**89/89 pruebas aprobadas sin omisiones; seis ZIP, 18 partidas, cero
avisos pendientes**. Los 16 JSON previos son idénticos a a2914ae. En f7af
se resuelven los tres avisos, conservando 34 episodios de PS confirmados,
9/9 mensajes enlazados y secuencias principales exactas de 23/23 y 48/48.
Total del corpus: 418 episodios confirmados y 132 mensajes asociados.
No se modifica el traductor de producción. Cero avisos no sustituye la
validación visual completa ni elimina las diferencias históricas de extracción.

## Prefijo rival incompleto en una narración de movimiento

En b121, partida 1, el anuncio de Wood Hammer se transcribe completo en
685 y 687–688, pero en 686 pierde una letra: `The opposng Rillaboom...`.
El detector crea un alias propio a partir de esa frase y asigna el candidato
a p1a. Eso cambia la última acción e impide consolidar las lecturas repetidas.
Las cuatro capturas muestran una animación del Rillaboom rival: en 05:42.00
todavía no hay texto visible; las siguientes sí muestran el anuncio completo.
No se altera el tiempo original de la traza para forzar coincidencia con la captura.

Antes de aplicar el movimiento, Ledger puede corroborar el marcador de lado:
se permite una diferencia de un carácter sólo en `the opposing`; nombre y
movimiento deben coincidir exactamente. Requiere un único actor rival activo,
dos lecturas completas ≥0,95 dentro del mismo anuncio continuo, sin cruzar
otras acciones, cambios de PS, entradas, turnos, huecos o retiradas. Se exploran
hasta tres segundos a cada lado. Un alias creado por el detector no basta
para tratar el prefijo defectuoso como mote; un mote con evidencia real del
HUD impide esta reasignación. Dos rivales posibles mantienen la ambigüedad.

La evidencia y candidato original quedan en `move_narration_support`. Sin
corroboración suficiente, la lectura sospechosa queda `move_text_unconfirmed`
y no sustituye la última acción ni incrementa la actividad del turno.
Con corroboración se corrige el lado antes de reutilizar la consolidación de
movimientos repetidos. No se cambia el texto original ni se aproxima el nombre
del actor o del movimiento por similitud.

En el caso revisado, 685 registra la acción de p2b, 686 corrige p1a→p2b
y se conserva como lectura repetida, y 687 también se consolida. El daño de
Dee Dee en 692 se vincula a la única acción 685. El lote pasa **4→3 avisos**;
siguen pendientes entrada/curación de Gori y separadores mixtos de Dee Dee.

**97/97 pruebas sin omisiones; siete ZIP y 20 partidas**. Los otros 19 JSON
son idénticos a 3c4e895. Se conservan 432 episodios de PS confirmados, 137
mensajes enlazados y uno pendiente. La partida corregida mantiene PS 14/14;
sus eventos principales pasan de 40 a 38, con 37/38 alineados porque permanece
la diferencia de orden de Defiant. Sólo cambia el prototipo, no producción.

## PS literales y entradas omitidas tras retirada voluntaria

La regla solicitada por Ies sustituye todas las reparaciones del separador de
PS propios. `177177`, `1771177`, `1777177`, `0./207` y cifras sin fracción no
son evidencia de PS, aunque su confianza OCR sea alta. Se conserva el valor
confirmado anterior; en una primera aparición sin lectura válida, se mantiene
la política explícita de máximo **inferido**, nunca confirmado. El candidato
original queda en `ignored_hp_reading` o `hp_rejected_reading` para auditoría.

Una fracción posterior válida puede confirmar la entrada antes de actuar,
incluso cuando el mote todavía no está en los alias globales. La búsqueda
se interrumpe ante acciones, retirada, fin de batalla o reemplazo. Si el
detector omite por deduplicación una fracción válida posterior a una lectura
rechazada, se recupera a su hora real, dentro de tres segundos y sin cruzar
acciones, entradas, turnos ni cambio de mote. No se completa la cifra defectuosa.
En b121, Dee Dee se confirma mediante `177/177` en **1206**; las variantes
1199–1205 no confirman sus PS. En f7af partida 1 se usa **198**, ya con `/`.

Las seis capturas de este corte confirman retirada de Dee Dee y anuncio
`Go! Gori!`. En 06:44.50 aún no hay anuncio visible en la captura; las de
06:45.30 y 06:46 sí lo muestran. No se modifica por ello el reloj de la traza.
Estas capturas no muestran PS: las cifras siguientes provienen del OCR archivado.

La recuperación general de una entrada voluntaria omitida exige retirada y
anuncio repetidos, slot único del mote saliente, HUD entrante corroborado y
alias conocido o habilidad repetida que identifique una única especie del
equipo archivado. Se exige continuidad, dos HUD consecutivos y extremos de PS
repetidos. Se conservan retirada, anuncio, equipo, habilidad y HUD en
`withdrawal_reconstruction`. No usa nombres de otros jobs ni tablas de motes.
El caso cubierto es una primera aparición propia tras retirada, con un único
entrante; evidencia ambigua o incompleta no crea la entrada. Las apariciones
con Ilusión o evidencia de habilidad copiada no se resuelven mediante habilidad.

En b121 partida 1: retirada **804–806**, entrada **810–813**, panel
`Gori’s Grassy Surge` **818–821**, único Rillaboom compatible del equipo;
HUD propio **852–855** y **873–875**. Se recuperan entrada, habilidad,
daño **207/207 inferidos →147/207 observados** y cura **147/207→159/207**,
con mensaje **876** enlazado por la regla de terreno existente. No se afirma
que se observaron 207/207 antes del golpe.

**101/101 pruebas sin omisiones, siete ZIP/20 partidas, cero avisos pendientes**
(frente a tres en a6011e6). Se conservan los 432 episodios anteriores y se añaden
dos de Gori: **434 con valor final confirmado; 138 mensajes enlazados**.
La validación estricta tiene dos efectos intencionales: f7af partida 2, entrada
885, pasa de máximo confirmado a inferido; en 904 partida 1, la cura 614–615
conserva **206/207** del último OCR válido y descarta `2071207`, antes reparado
como 207/207. El daño posterior parte de ese último valor confirmado.
Los demás valores de las transiciones anteriores se conservan. La lectura
`0./207` de 10 partida 2 se descarta en 2855 y el cero se confirma en **2856**,
a partir de `0/207` literal. No se pierde el debilitamiento.

Cero avisos es un resultado estructural, no validación visual completa del
vídeo. El prototipo sigue fuera de producción y sin exportación automática.

## Pruebas

```bash
CHAMPIONS_DIAGNOSTIC=/ruta/champions-diagnostics-10a7fba6fda04585.zip \
CHAMPIONS_DIAGNOSTIC_SECOND=/ruta/champions-diagnostics-90403f16712d4d41.zip \
CHAMPIONS_DIAGNOSTIC_THIRD=/ruta/champions-diagnostics-331e6e783c3e45a4.zip \
CHAMPIONS_DIAGNOSTIC_FOURTH=/ruta/champions-diagnostics-50808fa9e45e4ccc.zip \
CHAMPIONS_DIAGNOSTIC_FIFTH=/ruta/champions-diagnostics-9fd1afffbf8340df.zip \
CHAMPIONS_DIAGNOSTIC_SIXTH=/ruta/champions-diagnostics-f7af8655adb74bc6.zip \
CHAMPIONS_DIAGNOSTIC_SEVENTH=/ruta/champions-diagnostics-b121903688ad47a5.zip \
python3 -m unittest discover -s . -p 'test_champions_automaton.py' -v
```

Noventa y cuatro casos pequeños cubren causalidad, PS, conflictos OCR, separación de HUD,
megas asignadas al slot equivocado, identidades sin resolver y reentrada
fantasma. Incluyen PS sin confirmar, porcentajes divididos, descarte de separadores
malformados y recuperación de una lectura literal posterior, el aislamiento del 0 % del HUD de un compañero y
el seguimiento de Zoroark bajo Ilusión frente a un cambio normal de especie.
Siete pruebas de integración usan los cinco, cuatro, tres, dos, dos, dos y dos replays actuales,
respectivamente, y comprueban además la traza antigua con un actor anónimo y
un falso rebote desde cero.
Sin los ZIP se omiten sólo las pruebas de integración.
