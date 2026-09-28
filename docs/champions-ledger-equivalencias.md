# Equivalencias del traductor Champions Ledger

Primer bloque: 28 de septiembre de 2026. El flujo web usa el detector OCR compartido,
`BattleAutomaton` y `ledger_replay`. `showdown.py`, el acumulador y `_legacy_processor`
se conservan como referencia. Esta matriz registra comportamientos verificados;
la presencia de un tipo en el exportador no certifica todas sus variantes.

## Matriz de contratos

| Regla | Referencia anterior | Equivalencia nueva | Comprobación |
| --- | --- | --- | --- |
| Cambio forzado | `_event_lines`: `drag` | Emite `drag` y conserva identidad, PS, forma y estado del actor que vuelve. | Traza sintética por ambos autómatas; rechaza reentrada tras faint. |
| Crítico con objetivo conocido | `_event_lines`: `crit` | Emite `-crit` para el actor identificado. | Compara el protocolo con el serializador anterior; rechaza identidad contradictoria. |
| «A critical hit!» | `_with_known_crits` | Conserva el mensaje. Sólo asigna objetivo si hay un único actor dañado por la misma acción, próximo al anuncio. Puede usar el episodio completo de PS, aunque su lectura final llegue después. | Casos con un objetivo, dos objetivos, segundo daño tardío, cambio y límite de turno; cuatro replays reales recuperan un crítico. |
| Objeto revelado | `_event_lines`: `item` | Exporta `-item`, suprime la misma revelación repetida y admite nueva adquisición explícita tras perderlo. | Traza con revelación, repetición, consumo y readquisición. |
| Consumo y origen de curación | `enditem`, `[eat]`, `[from] item:` | Conserva las etiquetas y la causa observada durante toda la animación de PS. Fuentes contradictorias no se mezclan. | Sitrus Berry sintética y diagnósticos reales; primer punto intermedio etiquetado y último sin etiqueta. |
| Origen del campo | `[from] ability:`, `[of]` | Conserva habilidad y actor. Resuelve identidades provisionales/motes corroborados y valida el ocupante del slot. Guarda las etiquetas originales cuando las normaliza. | Trick Room, Psychic/Grassy Terrain reales; identidades provisionales y mote japonés sintéticos; origen falso rechazado. |
| Estados persistentes | `status`, `curestatus`, `cant` | Aplicación única por actor, curación explícita y bloqueo de sustitución contradictoria. Los PS exportados conservan el estado tras daño, curación de PS, cambio, reentrada, Mega e Ilusión. | Seis estados (`brn`, `par`, `slp`, `frz`, `psn`, `tox`) con daño, curación, salida y reentrada; incapacidad por parálisis; sueño en 7f41. |
| Fallos de ataque de área | `_with_known_miss`, `_with_known_target` | Ambos fallos, o daño a un rival y fallo sobre otro, conservan el atacante de la misma acción. No inventa un objetivo único para el movimiento. | Dos fallos, daño+fallo y cambio que invalida la acción. |
| Trick Room | Lectura de inicio/fin del OCR | Una sola transición por estado, reactivación después del cierre observado y origen del actor conservado. | Regresiones de 137129, duplicados, cancelación/reactivación y contradicciones. |
| Transición entre partidas | Separación de captura e índices de batalla | Un resultado residual sólo se excluye si precede a dos lecturas consecutivas de selección de equipos y a un nuevo inicio, sin actividad previa en el tramo excluido. El JSON conserva la evidencia y el contexto del replay usa los límites nuevos. | 137129/02 vuelve a exportar; resultado anterior con/sin evento, mismo rival con resultado contrario, selección insuficiente, actividad previa y partida truncada. Diagnóstico descargado reconstruye ambos replays. |
| Turnos, movimientos, PS, faint, Mega, habilidades, clima y Tailwind | Contratos y pruebas existentes | Se mantienen las validaciones de ambos autómatas. | Suites existentes y comparación de los ocho diagnósticos de control. No representa cobertura de todas las mecánicas posibles. |

El formato de PS y estado, `drag`, `crit` y las etiquetas de origen siguen el
[protocolo oficial de Showdown](https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md).
Las pruebas se basan en evidencia observada y resultados esperados, no en copiar
errores del ensamblador anterior.

## Pruebas permanentes

`backend/tests/test_champions_ledger_equivalence.py` recorre el flujo real
`documents_from_trace`: entrada OCR → Ledger → puente → documento del job.
Comprueba casos válidos y contradictorios. El inventario de tipos detecta si el
modelo antiguo añade un tipo sin declarar su tratamiento en el puente.

```bash
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -p 'test_champions_ledger*.py'
PYTHONPATH=backend/pkmn_vgc/champions_replay/prototype python3 -m unittest discover -s backend/pkmn_vgc/champions_replay/prototype -p 'test_*.py'
```

Los casos reales opcionales se habilitan con las variables `CHAMPIONS_DIAGNOSTIC_*`
ya usadas por las suites; su omisión se informa explícitamente.

## Comparación del corpus

Control: `7f41`, `433c`, `3432`, `a026`, `c7f`, `b3f7(1)`, `da3f` y `137129/01`.
Mantienen acciones, orden, cantidades de PS, identidades, turnos y resultado.
Las diferencias de protocolo corresponden a críticos observados, etiquetas de
origen/consumo y el estado junto a los PS. Los índices internos pueden cambiar
al conservar un mensaje nuevo; las pruebas comprueban la referencia causal,
no un número de secuencia fijo.

## Pendientes explícitos

- `terastallize`: permanece sin exportación en este bloque; requiere un caso
  del proyecto que permita validar también forma, reentrada y estado. No se
  omite silenciosamente ni se activa por completar el inventario.
- Ampliar los diagnósticos reales de cambios forzados, objetos adquiridos y
  estados distintos del sueño; las variantes nuevas tienen pruebas sintéticas.
- Revisar en la ROG la representación visual y el reanálisis de este bloque.
- Continuar la revisión de variantes de clima/campo, condiciones laterales y
  mensajes de combate. No se declara equivalencia completa de todos los casos.

## Reanálisis de 137129: delimitación corregida

El ZIP recibido el 28 de septiembre a las 12:28 (CDMX) conservaba un resultado
de la primera partida desde el fotograma 1122 dentro del índice de la segunda.
Ledger lo aceptaba como cierre y después el puente rechazaba el turno 1.
La selección nueva en 1214/1215 y el inicio en 1354 acreditan la separación:
el tramo 1122–1213 queda documentado fuera de la segunda partida. Su cierre
válido está en 1825, turno 4, contra LemonLime. El replay de la primera partida
se conserva exactamente. Los ocho replays de control siguen idénticos.

La regresión real se activa con `CHAMPIONS_DIAGNOSTIC_BOUNDARY_137129`, apuntando
al ZIP del reanálisis nuevo. Las tres regresiones sintéticas no requieren ZIP.
No se eliminan filas de la traza, no se toma el ganador anterior y se conserva
el bloqueo si hay acciones reales después de un cierre sin separación probada.

## Instalación

Actualizar `desarrollo`, reiniciar app y servicio Python, y usar **Reanalizar
vídeo**. El cambio no reescribe automáticamente los replays guardados. No requiere
dependencias nuevas.
