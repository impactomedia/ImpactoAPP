# Impacto APP 1.3.1 — Corrección de CI

Este parche corrige únicamente la ejecución de pruebas automáticas en GitHub Actions.

## Cambio
- Ejecuta pytest mediante `python -m pytest -q`.
- Define `PYTHONPATH=.` para que el repositorio raíz esté disponible al importar `app`.

## Importante
- No modifica MySQL.
- No requiere migración.
- No requiere Pre-Deploy Command en Railway.
- No cambia lógica funcional de la aplicación.
