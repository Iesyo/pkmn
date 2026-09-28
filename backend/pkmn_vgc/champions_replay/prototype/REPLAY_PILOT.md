# Champions Ledger → replay: primer piloto

`ledger_replay.py` transforma **una partida cerrada** de `battle-XX.json` en
`.log`, `.json` y `.html` para el visor de Pokémon Showdown. Se ejecuta fuera
del pipeline de COL-102. Los eventos y su orden vienen exclusivamente de
Ledger; el ZIP original aporta los nombres de los jugadores, el formato y
el resultado OCR. No lee `output/replay-*.log` ni usa el replay archivado como
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
movimiento/debilitamiento** observados y escritos, sin diferencias de orden,
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
  al rival. No emite Team Preview ni decisiones `inputlog` sin evidencia.
- Exige un único anuncio OCR posterior al fin y dentro de la misma batalla
  que nombre al rival como ganador o perdedor. Si falta, **no fabrica `|win|`**:
  la exportación falla indicando la evidencia faltante.
- En esta primera etapa se traducen entradas, turnos, habilidades, campo,
  clima, megaevoluciones, movimientos, daños, curas, debilitamientos y cierre.
  Los demás tipos de Ledger deben recibir traductor y pruebas antes de usar
  otra partida. No hay integración automática con el generador de producción.

El HTML carga el visor oficial mediante su script público; necesita conexión
al abrirse. La comprobación estructural y contra OCR no sustituye una
revisión visual del reproductor.
