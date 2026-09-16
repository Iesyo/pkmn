# Modelos de Battle Lab

Coloca aquí el ZIP del modelo promovido, sin descomprimirlo.

Modelo actual: [mc-20260916-bc-ppo.zip](https://drive.google.com/file/d/1B8-CutEs9Eb2CO-hMnXKx0KVXH6q3e5G/view).
También puede conservar su nombre original de Colab, `step-000196608.zip`.
El instalador reconoce el contenido por tamaño y SHA-256, aunque haya otros ZIP.
La versión esperada está en `battle_lab/model_release.json`.

Desde la raíz del proyecto, con el runtime de Battle Lab detenido:

```powershell
.\.venv-battle-lab\Scripts\python.exe -m battle_lab.model_release install --runtime-root .\.battle-lab-runtime
```

El instalador toma el archivo de esta carpeta, respalda el modelo anterior y
prepara la copia activa en `.battle-lab-runtime/models/`. Reinicia tu comando
habitual de Nana/LAN y comprueba desde otra terminal:

```powershell
.\.venv-battle-lab\Scripts\python.exe -m battle_lab.model_release verify --runtime-root .\.battle-lab-runtime
```

Los modelos de esta carpeta quedan excluidos de Git. Las instrucciones completas
y el comando para volver al modelo anterior están en
[LOCAL_SPARRING.md](../battle_lab/LOCAL_SPARRING.md).
