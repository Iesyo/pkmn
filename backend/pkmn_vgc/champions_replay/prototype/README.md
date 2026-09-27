# COL-102 · Autómata temporal de batalla

Este prototipo lee **sólo** `ocr.trace.jsonl` del diagnóstico y produce un
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
   estado, objeto perdido, terreno activo y última acción. Resuelve la especie
   de los actores anónimos con la asociación final de la propia traza.
3. Un anuncio `sent out`/`Go!` fija el momento lógico de una entrada que el
   HUD confirma más tarde. La entrada queda antes de sus habilidades y de los
   movimientos posteriores; se conservan ambos tiempos. Los cuatro leads se
   representan en orden estable `p1a,p1b,p2a,p2b`.
4. Consolida lecturas sucesivas de PS durante una animación en **un episodio**
   y deja todos los valores intermedios en `observations`. Agrupa el mismo
   aviso de movimiento leído en varios frames; los mensajes narrativos se
   adjuntan al evento próximo. Una oscilación de PS que vuelve al valor previo
   antes de actuar queda en `review`, sin producir daño y cura inventados.
   La lectura candidata pasa por un estado **provisional**: se buscan el
   valor completo y su posición en el HUD del slot durante el fotograma y los
   dos muestreados siguientes. Se aceptan números y `%` separados pero
   contiguos, y separadores `/` mal reconocidos cuando hay corroboración
   suficiente. Un número solo, aunque se repita, no confirma un porcentaje.
   Una lectura sin apoyo queda en `hp_unconfirmed`: no cambia los PS del
   actor y obliga a revisar la transición siguiente. Las entradas sin PS
   confirmados también conservan el candidato en `observations`, con PS de
   entrada desconocidos.
5. Si dos números OCR se contradicen en el mismo HUD, conserva ambos como
   evidencia. La lectura aislada contradicha queda en `hp_ocr_conflict`, sin
   modificar los PS ni generar daño/cura. No mezcla números de HUD separados.
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
   el PS de entrada queda **desconocido**.
7. Compara la secuencia de eventos principales y episodios de PS con los
   replays archivados de ambos jobs. Los desacuerdos se informan; no se
   arreglan copiando la salida anterior.

`consistent` significa **sin contradicción estructural detectada**. No
significa que el evento esté confirmado visualmente. `review` requiere mirar
el fotograma o investigar una causa; `suppressed` conserva la lectura en el
JSON pero la excluye de la cronología propuesta. El prototipo **no** exporta
todavía un replay Showdown: sería prematuro mientras haya discrepancias.
En los eventos pertinentes, `hp_state` distingue `confirmed`, `unconfirmed`,
`unknown` y `rejected`.
`hp_support` guarda la evidencia OCR y la razón de la confirmación o rechazo;
el estado del actor sólo contiene PS confirmados, o `null` si la lectura
actual no quedó establecida. `report.json` separa episodios, observaciones
confirmadas, lecturas sin confirmar y transiciones que requieren revisión.

## Primer ZIP · 10a7fba6fda04585

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
partida 5, revisar las entradas de Pelipper y Rillaboom anunciadas antes de
los frames **5550 y 5826**: el PS leído en la confirmación no demuestra el
PS con el que entraron.

## Segundo ZIP · 90403f16712d4d41

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
| 3 | 150 → 84 | 46/46 | 31/31 | Una lectura de daño subiría los PS de Kingambit de 1 % a 85 % tras un cambio con Zoroark-Hisui; queda en revisión. |
| 4 | 114 → 67 | 42/42 | 17/17 | Indeedee-F mantiene la parálisis. Un texto `can't use` sin acción identificable queda en revisión. |

Los PS `1%` de Sneasler y `3%` de Delphox parecen recortes OCR de menor confianza,
no evidencia suficiente para crear daño y posterior curación. El `44` leído
junto al `1%` de Sneasler tampoco prueba por sí solo cuál fue su PS exacto en
ese instante. El replay archivado de la partida 2 contiene dos episodios de
PS más que el registro propuesto; no se toman como verdad visual.

La coincidencia de eventos principales **no prueba fidelidad**: el autómata
parte de candidatos del mismo detector que creó los replays archivados. Faltan
un extractor independiente de observaciones puras, atribución causal completa
de PS y una revisión visual de las discrepancias restantes.
Este corte establece la frontera entre observación, evento consolidado y
emisión de Showdown para continuar sin reprocesar el vídeo.

En las **nueve partidas actuales**, los 259 episodios de PS propuestos y no
suprimidos conservan evidencia OCR verificable en su HUD; los 465 eventos
principales mantienen el orden de sus replays archivados. Ninguno de esos 259
episodios necesitó pasar por `hp_unconfirmed`; se detectaron y conservaron en revisión
los conflictos y transiciones dudosas descritos arriba. Este conjunto prueba
que la puerta no descartó esas lecturas válidas, **no** que pueda evitar toda
lectura parcial en futuros vídeos: un OCR erróneo, completo y persistente puede
requerir una observación adicional o revisión visual.

## Pruebas

```bash
CHAMPIONS_DIAGNOSTIC=/ruta/champions-diagnostics-10a7fba6fda04585.zip \
CHAMPIONS_DIAGNOSTIC_SECOND=/ruta/champions-diagnostics-90403f16712d4d41.zip \
python3 -m unittest discover -s . -p 'test_champions_automaton.py' -v
```

Quince casos pequeños cubren causalidad, PS, conflictos OCR, separación de HUD,
megas asignadas al slot equivocado, identidades sin resolver y reentrada
fantasma. Incluyen PS sin confirmar, porcentajes divididos, corrección
corroborada del separador y el aislamiento del 0 % del HUD de un compañero.
Dos pruebas de integración usan los cinco y cuatro replays actuales,
respectivamente, y comprueban además la traza antigua con un actor anónimo y
un falso rebote desde cero.
Sin los ZIP se omiten sólo las pruebas de integración.
