# Modelos de Battle Lab

Coloca aquí el ZIP del modelo promovido, sin descomprimirlo.

Modelo productivo actual: [step-000786432.zip](https://drive.google.com/file/d/1vvKQJv59lmoYqjRVE0r-APC-JToEpQLb/view), de la corrida NORMAL `20260922T182640086766Z`.
SHA-256: `93f3d185e7f5b9e32d1fa3c4a64defac9dd86152f166660b4508d911e49fa0ab`.

El instalador reconoce el contenido por tamaño y SHA-256 aunque haya otros ZIP en esta carpeta. El manifiesto está en `battle_lab/model_release.json`.

Con el runtime de Battle Lab detenido, desde la raíz del proyecto:

```powershell
.\.venv-battle-lab\Scripts\python.exe -m battle_lab.model_release install --runtime-root .\.battle-lab-runtime
```

El instalador respalda el productivo anterior y prepara el nuevo en la ruta activa compatible `.battle-lab-runtime/models/step-000196608.zip`. Este nombre de ruta histórico no describe los pesos cargados: comprueba el SHA.

Reinicia Nana/LAN **sin** el argumento temporal `--checkpoint .\models\step-000786432.zip` y verifica desde otra terminal:

```powershell
.\.venv-battle-lab\Scripts\python.exe -m battle_lab.model_release verify --runtime-root .\.battle-lab-runtime
```

Los ZIP de esta carpeta quedan excluidos de Git. El rollback y la memoria de Nana se describen en [LOCAL_SPARRING.md](../battle_lab/LOCAL_SPARRING.md).
