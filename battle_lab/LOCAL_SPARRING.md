# Battle Lab — Sparring local

`War Room > Sparring` conecta la interfaz de LikeNoOneEverWas con un runtime local. Pokémon Showdown resuelve el combate, el checkpoint LIGHT M-C controla al rival y el **cliente clásico oficial de Pokémon Showdown** renderiza el room real como espectador: campo, sprites, animaciones y battle log.

## Restricción del pool

Sparring no completa ni infiere sets. Para poder entrar al pool rival, el PokéPaste debe contener exactamente seis Pokémon y cada bloque debe declarar explícitamente:

- objeto;
- habilidad;
- nivel;
- Stat Points/EVs;
- naturaleza;
- exactamente cuatro movimientos.

El filtro de interfaz descarta los pastes incompletos antes de intentar una partida y el servicio local vuelve a aplicar el mismo gate. Después, Pokémon Showdown valida legalidad para `gen9championsvgc2026regmc`; un paste completo pero ilegal también se rechaza.

Las fuentes habilitadas para el rival son **VGCPastes** y **Mis pastes**. Fuentes sin PokéPaste exacto recuperable quedan fuera del pool.

## Arquitectura local

El runtime usa tres puertos exclusivamente locales:

```text
127.0.0.1:8765  API de Battle Lab / poke-env
127.0.0.1:8766  servidor privado de Pokémon Showdown
127.0.0.1:8767  cliente clásico oficial de Pokémon Showdown
```

La aplicación web habla con `8765` a través del proxy same-origin `/api/battle-lab/...`. El renderer clásico se carga en un `iframe` de War Room y se conecta directamente al room de batalla en el servidor local `8766`.

El cliente visual **no se copia ni se modifica dentro de este repositorio**. `battle_lab.local_runtime` prepara un checkout independiente en `.battle-lab-runtime/pokemon-showdown-client`, fijado al commit:

```text
e47b8be4103b5e027cd191a024e383be88f37bfe
```

Ese checkout conserva su licencia **AGPLv3** y se sirve sin modificar desde loopback. LikeNoOneEverWas únicamente lo orquesta y lo muestra como renderer local. La legalidad de las decisiones humanas sigue viniendo de `battle.valid_orders` en `poke-env`.

El cliente clásico entra al room como **espectador**. Por eso el campo animado y el battle log son los reales de Showdown, mientras los botones que envían la jugada siguen siendo controles propios de War Room construidos únicamente a partir de órdenes legales. Esto evita tener dos clientes intentando controlar el mismo jugador y mantiene una sola autoridad de decisión.

## Checkpoint canónico

Modelo: `step-000196608.zip`

Drive: https://drive.google.com/file/d/1hmKrYaLg5u0aUpzuxtA3ZwWUpz-w9c6_/view

SHA-256 esperado:

```text
fa8687d08feeb169f4eb4f4a078b65971346e2ef5b0ca0ff899e811721075759
```

Ruta original en el entrenamiento de Colab:

```text
/content/drive/MyDrive/Colabs/LikeNoOneEverWas/BattleLab/MC-Training/training/rl/light/seed260913/checkpoints/step-000196608.zip
```

No se versiona el checkpoint en Git.

## Preparación local

Desde la raíz del repositorio, crea un entorno dedicado. En Windows/ROG Ally:

```powershell
py -3.11 -m venv .venv-battle-lab
.\.venv-battle-lab\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r battle_lab/requirements-local.txt
```

Si PowerShell bloquea `Activate.ps1`, se puede usar directamente:

```powershell
.\.venv-battle-lab\Scripts\python.exe
```

Crea la carpeta local de modelos y copia ahí el checkpoint descargado desde Drive:

```powershell
New-Item -ItemType Directory -Force .battle-lab-runtime\models
```

El runtime completo vive en `.battle-lab-runtime/`, que está ignorado por Git. La primera ejecución prepara los checkouts fijados de Pokémon Showdown, VGC-Bench y Pokémon Showdown Client; las ejecuciones posteriores los reutilizan.

## Arranque canónico

Usa **`battle_lab.local_runtime`**, no `local_sparring_service` directamente:

```powershell
.\.venv-battle-lab\Scripts\python.exe -m battle_lab.local_runtime `
  --runtime-root .\.battle-lab-runtime `
  --checkpoint .\.battle-lab-runtime\models\step-000196608.zip
```

La primera ejecución del launcher puede tardar porque clona, instala y compila el cliente oficial de Showdown. El progreso se muestra en consola. Después debe imprimir, entre otros mensajes:

```text
Renderer clásico listo en http://127.0.0.1:8767/play.pokemonshowdown.com/testclient-old.html
Uvicorn running on http://127.0.0.1:8765
```

Comprobaciones rápidas:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/model-info
```

`/model-info` fuerza la preparación del motor y carga el checkpoint. El renderer se prepara al arrancar `local_runtime`.

## Flujo interactivo

1. Arranca la aplicación web local con `npm run dev`.
2. Arranca `battle_lab.local_runtime`.
3. Abre `War Room > Sparring`.
4. Selecciona el Team o conserva la variante actual de Optimizar; Sparring usa el `workingTeam`, por lo que respeta cambios todavía no guardados.
5. Pulsa `Buscar rival y pelear`.
6. El selector recorre el corpus en orden aleatorio y salta automáticamente cualquier paste incompleto o no recuperable.
7. En Team Preview se muestran **los dos equipos completos**. Elige cuatro Pokémon; 1-2 son lead y 3-4 backline.
8. Al comenzar el combate, War Room embebe el room real del cliente clásico de Showdown. Ahí deben verse las animaciones, HP, cambios, efectos de campo y el log legible de movimientos/daño/estados.
9. Debajo del renderer, War Room presenta únicamente decisiones que existen en `battle.valid_orders`: movimiento, mecánica, objetivo y switch.
10. Confirma el turno; el renderer de Showdown debe animar inmediatamente el resultado y agregar el evento al battle log.

## Criterio de aceptación

El BO1 local queda validado cuando:

- ambos equipos se ven completos en Team Preview;
- el room clásico de Showdown se carga dentro de War Room;
- los movimientos de ambos lados aparecen en el battle log real;
- daño, estados, switches y Mega/Tera se reflejan visualmente y con animación;
- las decisiones humanas siguen limitadas a órdenes legales del motor;
- la batalla termina correctamente y conserva el resultado/replay.

Después de ese smoke se continúa con BO3/rematch/gauntlet y las mediciones 1/10/100 de `BATTLE-LAB-LOCAL-INTEGRATION-001`.
