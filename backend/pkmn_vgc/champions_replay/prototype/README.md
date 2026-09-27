# Champions Ledger · Autómata temporal de batalla

**Champions Ledger** es un proyecto independiente nacido de los diagnósticos
de COL-102. Su primera versión lee **sólo** `ocr.trace.jsonl` y produce un
registro intermedio por batalla. No requiere el vídeo, OCR adicional, Qwen ni
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
   contiguos, y separadores `/` mal reconocidos cuando hay corroboración
   suficiente. Un número solo, aunque se repita, no confirma un porcentaje.
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
  segundo ZIP, partida 2, frames 1694 y 1766: el `28` de mayor confianza
  coincide con los `28%` completos de fotogramas anteriores. Estas dos
  decisiones provienen de la traza OCR, sin confirmación visual de esos
  fotogramas. El `1%` de Sneasler junto a `44` en frame 2082 permanece en
  revisión porque `44` no coincide con los PS previos registrados.
- Recuento actual: **8 incidencias** en 12 partidas (primer ZIP: 4;
  segundo: 4; tercero: 0), frente a 11 antes de esta corrección. Siguen
  siendo **328 episodios de PS** propuestos y no cambia el orden principal
  alineado con los replays archivados. Las lecturas suprimidas permanecen en
  JSON para auditoría.

## Rillaboom · 27 de septiembre

- La captura aportada a las **23:17.04** muestra **88 %** en el HUD de
  Rillaboom. No coincide exactamente con el frame 2796 (23:17.5), pero es
  contigua: los frames 2793–2795 muestran `88%`; en 2796 el OCR leyó `88`
  (confianza .99996) superpuesto a `0%` (.78096); en 2798 aparece otra vez
  `88%` completo. No hay acción entre estas lecturas. El rebote inventado
  en 2797 se conserva como `hp_rejected_reading` suprimido, con ambas
  observaciones y evidencia antes y después. Rillaboom sigue a 88 %.
- En los mismos 12 combates quedan **7 incidencias** (primer ZIP: 3;
  segundo: 4; tercero: 0), **328 episodios de PS** y las mismas alineaciones
  de eventos principales. Sólo el conflicto `1%` frente a `44` de Sneasler
  en el segundo ZIP, partida 2, frame 2082 requiere inspección visual para
  resolver sus PS. Seis avisos son candidatos ya suprimidos y auditables.

## Cura de Sneasler por terreno · 27 de septiembre

- Capturas del job `90403f16712d4d41`, partida 2: a las **17:20.50**
  Sneasler tiene **41 %**; a las **17:21.50**, **47 %**. La pantalla muestra
  el efecto verde y en frame 2085 (17:22) aparece «The opposing Sneasler
  had its HP restored.» Grassy Terrain seguía activo. El `1%` del frame
  2082 era un recorte falso; el `44` OCR coexistente tampoco fija PS nuevos.
- Ledger guarda el fragmento como `hp_rejected_reading` suprimido y emite
  **una cura 41 → 47 %**, causa «Grassy Terrain corroborado por HUD y
  mensaje». Exige que el incremento corresponda a ~1/16, que el PS final
  tenga OCR completo y que el mensaje nombre al mismo actor. Sin esas
  pruebas conserva el conflicto para revisión.
- Quedan **6 avisos** auditables en 12 partidas (primer ZIP: 3; segundo: 3;
  tercero: 0), todos por candidatos suprimidos; ningún conflicto de PS
  pendiente en estas trazas. Persisten **328 episodios de PS** y las mismas
  alineaciones principales con los replays archivados. La prueba ciega y la
  validación del replay generado siguen pendientes.

## Pasada retrospectiva · 27 de septiembre

La propuesta de Ies de mirar hacia atrás se aplica después de la primera
reconstrucción cronológica. En estas trazas, las 89 narraciones de cura llegan
1–2 s después del primer cambio detectado; las cinco de quemadura, 0,5–1 s
antes. De las 19 de retroceso, cinco llegan antes, 13 en el mismo fotograma
muestreado y una después. La asociación admite ambos órdenes.

| Job | Partidas | Mensajes asociados | Episodios de PS | Avisos auditables |
| --- | ---: | ---: | ---: | ---: |
| `10a7fba6fda04585` | 5 | 57/57 | 146 | 3 |
| `90403f16712d4d41` | 4 | 39/39 | 112 | 3 |
| `331e6e783c3e45a4` | 3 | 17/17 | 70 | 0 |
| **Total** | **12** | **113/113** | **328** | **6** |

- Corrige tres asociaciones: quemadura de Indeedee-F, mensaje 369 → daño
  371 en job `10a7`, partida 1; quemaduras de Gardevoir, mensajes 3064 y
  3181 → daños 3067 y 3183 en job `90403`, partida 3. El primer mensaje
  estaba en una lectura descartada y los otros dos en curaciones anteriores.
  Los tres daños ya existían; ahora conservan la narración y causa correctas.
- Los 19 episodios de retroceso quedan identificados explícitamente por su
  mensaje, en lugar de depender de la última acción cercana.
- Comparación completa con el corte anterior: idénticos actores, PS, orden,
  estados de los eventos y seis incidencias por candidatos suprimidos. Los
  tres enlaces corregidos eran errores de atribución que ese recuento de
  incidencias no mostraba. Se conservan las alineaciones anteriores con los
  replays archivados. La cura de Sneasler sigue siendo una sola, 41 → 47 %.
- **45 pruebas aprobadas**, incluyendo los tres ZIP y escenarios de
  ambigüedad, separación por turno/acción/reentrada, falta de identidad o PS
  confirmados, mensajes repetidos y retroceso narrado después del faint.
- Alcance actual: las tres frases inglesas de PS reconocidas en los ZIP.
  Otros mensajes mantienen el tratamiento existente. Una ventana fuera de
  3 s queda pendiente; estos resultados aún requieren prueba con vídeos
  nuevos y validación visual independiente. Sigue siendo un prototipo fuera
  del pipeline de producción.

## Pruebas

```bash
CHAMPIONS_DIAGNOSTIC=/ruta/champions-diagnostics-10a7fba6fda04585.zip \
CHAMPIONS_DIAGNOSTIC_SECOND=/ruta/champions-diagnostics-90403f16712d4d41.zip \
CHAMPIONS_DIAGNOSTIC_THIRD=/ruta/champions-diagnostics-331e6e783c3e45a4.zip \
python3 -m unittest discover -s . -p 'test_champions_automaton.py' -v
```

Cuarenta y dos casos pequeños cubren causalidad, PS, conflictos OCR, separación de HUD,
megas asignadas al slot equivocado, identidades sin resolver y reentrada
fantasma. Incluyen PS sin confirmar, porcentajes divididos, corrección
corroborada del separador, el aislamiento del 0 % del HUD de un compañero y
el seguimiento de Zoroark bajo Ilusión frente a un cambio normal de especie.
Tres pruebas de integración usan los cinco, cuatro y tres replays actuales,
respectivamente, y comprueban además la traza antigua con un actor anónimo y
un falso rebote desde cero.
Sin los ZIP se omiten sólo las pruebas de integración.
