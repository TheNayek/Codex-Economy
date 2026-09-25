# Codex Economy

**¿La cuota de Codex te dura demasiado poco? Dale una forma de elegir modelos y dedicar esfuerzo según la tarea.**

[![Pruebas](https://github.com/TheNayek/Codex-Economy/actions/workflows/ci.yml/badge.svg)](https://github.com/TheNayek/Codex-Economy/actions/workflows/ci.yml)
[English](README.md) · [Licencia MIT](LICENSE)

Codex Economy es un kit instalable para quien usa Codex pero no sabe cuándo
delegar trabajo acotado, aumentar el razonamiento o ejecutar una tarea completa
con un modelo pequeño. Un responsable Astra conserva las decisiones mientras
agentes Luna o Sol resuelven partes definidas. Pásale este
repositorio a Codex: inspeccionará tu entorno, adaptará los perfiles, aplicará
la configuración y comprobará el resultado.

## Dale esto a Codex

```text
Instala Codex Economy siguiendo
https://github.com/TheNayek/Codex-Economy/blob/main/INSTALL.md

Inspecciona mi configuración y los modelos disponibles. Adapta DEFAULT, QUICK,
DEEP, DIRECT y CONTINUITY a mi cuenta. Conserva mis permisos, integraciones y preferencias
existentes. Aplica y verifica la configuración. Explícame cuándo utilizar
cada perfil y cómo deshacer la instalación.
```

[INSTALL.md](INSTALL.md) contiene el procedimiento para el agente, respaldado
por código y pruebas. No hace falta una API key, servicios residentes ni
dependencias de Python.

## Qué perfil usar

| Tarea | Punto de partida |
| --- | --- |
| Funcionalidad abierta con partes independientes y acotadas | **DEFAULT**: responsable Astra high |
| Trabajo ligero que requiere criterio de Astra | **QUICK**: Astra medium |
| Tarea completamente acotada y verificable | **DIRECT**: Luna high |
| Trabajo complejo de alcance conocido o continuidad útil de Sol | **CONTINUITY**: Sol medium |
| Necesidad concreta de mayor razonamiento | **DEEP**: Astra xhigh, opcional |

La plantilla propone Astra high como responsable habitual, con agentes Luna para
trabajo acotado y Sol medium para implementación compleja. Son puntos de partida: tu agente debe comprobar qué modelos y
esfuerzos admite tu cuenta. No representan un ahorro demostrado.
DEEP xhigh requiere que el modelo y la cuenta lo admitan; si no, elige un
esfuerzo compatible en el manifiesto local.

La política instalada también limita contexto y delegación innecesarios.
Incluye reintentos, revisión y correcciones en el coste del trabajo. Puede
recomendar un perfil para la siguiente tarea; no cambia en secreto el modelo
que ya está ejecutando el turno actual. Sol high y Astra xhigh requieren un motivo concreto.

## Una idea de la diferencia potencial

Para un **ejemplo de consumo idéntico** de 20.000 tokens de entrada sin caché,
80.000 de entrada con caché y 10.000 de salida a velocidad Standard, las tarifas
oficiales consultadas el 25/09/2026 dan:

| Modelo | Créditos calculados para esos tokens |
| --- | ---: |
| GPT-6 Astra | 19,500 |
| GPT-6 Sol | 3,900 |
| GPT-6 Luna | 0,195 |

Son cálculos de créditos, **no ahorro medido ni una conversión al porcentaje de
cuota de tu suscripción**. Tampoco suponen que los tres modelos resuelvan igual
una tarea con esos tokens.

Un ejemplo con un responsable Astra y agentes Luna o Sol, que incluye revisión
y coordinación, figura en la guía. Son cálculos hipotéticos, no un rango de
ahorro esperado. Consulta la
[fuente oficial, fórmula y supuestos](docs/MEASURING.md#worked-estimates-what-could-model-selection-change).

## Instalación manual

Python **3.11+** y un cliente Codex compatible. Usa `python3` si corresponde.

```sh
git clone https://github.com/TheNayek/Codex-Economy.git
cd Codex-Economy
python -m unittest discover -s tests -q
python economy.py self-test
```

Elige una opción:

```powershell
# Windows PowerShell
python economy.py init --home "$env:USERPROFILE\.codex"
```

```sh
# macOS / Linux
python economy.py init --home "$HOME/.codex"
```

Esto solo crea `manifest.local.json` junto al script. Revisa modelos y esfuerzos
antes de aplicar. Para probar sin tocar tu instalación, elige una ruta absoluta
desechable dentro de un workspace.

```sh
python economy.py doctor --account main
python economy.py plan --account main
python economy.py sync --account main
python economy.py verify --account main
```

Abre una tarea nueva. En CLI: `codex --profile DEFAULT`, `QUICK`, `DEEP`,
`DIRECT` o `CONTINUITY`.
En Desktop selecciona el modelo/esfuerzo correspondiente en el compositor;
los perfiles CLI no añaden botones a la aplicación.

## Qué cambia y cómo volver atrás

`sync` administra cinco perfiles, siete roles, cuatro campos de agentes y el bloque
marcado de AGENTS.md. Conserva tus preferencias de modelo principal, permisos,
búsqueda web e integraciones. No copia credenciales. Las colisiones con archivos
ajenos se rechazan. [Detalles de seguridad](SECURITY.md).

Aplicar DEFAULT al modelo principal requiere una acción explícita:

```sh
python economy.py plan --account main --include-defaults
python economy.py set-defaults --account main
python economy.py verify --account main --include-defaults
```

```sh
python economy.py recover --account main
python economy.py rollback --account main
```

`recover` recupera una transacción interrumpida; `rollback` deshace la última
completada. Para desinstalar, revierte las transacciones en orden inverso hasta
el estado anterior. Conserva los backups en `CODEX_HOME/economy-backups` hasta
verificar. Borrar el checkout no desinstala los cambios.

Al actualizar, conserva tu manifiesto local y revisa las novedades antes de
sincronizar. Los roles nuevos no se fusionan automáticamente.
[Compatibilidad](docs/COMPATIBILITY.md).

## Medir sin vender humo

No prometemos un porcentaje de ahorro. Un modelo pequeño con muchos reintentos
puede gastar más. Usa el [protocolo de comparación](docs/MEASURING.md) y la
[plantilla CSV](examples/measurement.csv). Tokens, créditos y cuota son distintos.

El observador local omite conversaciones, pero puede mostrar metadatos
privados. El smoke opcional consume cuota real y solo comprueba routing.
Las pruebas automatizadas usan datos sintéticos y no ejecutan modelos.

¿Quieres dos cuentas simultáneas? Ese es el objetivo de
[**Codex Dual**](https://github.com/TheNayek/Codex-Dual), un proyecto independiente.
Economy funciona con una sola cuenta.

[Contribuir](CONTRIBUTING.md) · [Licencia MIT](LICENSE).
Proyecto independiente, sin afiliación con OpenAI.
