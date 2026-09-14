# Battle Lab — Sparring local

`War Room > Sparring` conecta la interfaz de LikeNoOneEverWas con un servicio Python local en loopback. Pokémon Showdown resuelve el combate y el checkpoint LIGHT M-C controla al rival.

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

Crea la carpeta local de modelos y copia ahí el checkpoint descargado desde Drive:

```powershell
New-Item -ItemType Directory -Force .battle-lab-runtime\models
```

El runtime completo vive en `.battle-lab-runtime/`, que ya está ignorado por Git. La primera ejecución prepara checkouts fijados de Pokémon Showdown y VGC-Bench dentro de esa carpeta; las ejecuciones posteriores los reutilizan.

## Arranque

Con el entorno virtual activo:

```powershell
python -m battle_lab.local_sparring_service `
  --runtime-root .battle-lab-runtime `
  --checkpoint .battle-lab-runtime\models\step-000196608.zip
```

El servicio escucha únicamente en:

```text
http://127.0.0.1:8765
```

Pokémon Showdown interno usa `127.0.0.1:8766`. El servicio no mata procesos ajenos: si ese puerto ya está ocupado, aborta y lo reporta.

Comprobaciones rápidas:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/model-info
```

`/model-info` fuerza la preparación del runtime y carga el checkpoint. La primera llamada puede tardar porque instala/compila el checkout fijado de Showdown y prepara VGC-Bench.

## Primer smoke interactivo

1. Arranca la aplicación web local.
2. Abre `War Room`.
3. Selecciona el Team o conserva la variante actual de Optimizar; Sparring usa el `workingTeam`, por lo que respeta cambios todavía no guardados.
4. Entra a `Sparring`.
5. Confirma que aparece `Loopback conectado` / `Battle Lab listo`.
6. Pulsa `Buscar rival y pelear`.
7. El selector recorre el corpus en orden aleatorio y salta automáticamente cualquier paste incompleto o no recuperable.
8. Elige cuatro Pokémon en Team Preview. Los dos primeros son lead y los dos últimos backline.
9. Durante cada turno la UI solo muestra combinaciones presentes en `battle.valid_orders`; no fabrica movimientos, targets ni switches.
10. Completa la batalla y conserva el resultado/replay para la validación del spike local.

## Criterio de aceptación del MVP

El MVP queda validado cuando una partida BO1 completa puede jugarse desde War Room entre el usuario y LIGHT M-C sin inferir ningún campo del rival, con ambos Teams aceptados por Showdown y con las decisiones humanas enviadas únicamente mediante órdenes legales generadas por el motor.

Después de ese smoke se puede continuar con BO3, rematch, gauntlet y las mediciones 1/10/100 previstas en `BATTLE-LAB-LOCAL-INTEGRATION-001`.
