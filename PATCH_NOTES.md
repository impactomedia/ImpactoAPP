# Impacto APP 1.7.1 — Hotfix Fase 4

## Problema detectado
GitHub Actions de la v1.7.0 ejecutó 70 pruebas:
- 69 pasaron.
- 1 falló.

La única falla fue que la prueba esperaba la plataforma `google_business`, pero el parser no reconocía la etiqueta "Google Business Profile / Maps" porque `google` no estaba incluido en `_PLATFORM_LABELS`.

## Corrección
Se agregó:

`"google_business": ("google business", "google", "maps")`

al mapa de plataformas.

## Base de datos
No hay cambios de esquema.
No hay nueva migración.

## Railway
No necesitas agregar un nuevo Pre-Deploy para la v1.7.1.

Si todavía tienes configurado el Pre-Deploy de Fase 4:
`python -m scripts.upgrade_clients_v2_phase4`

puede ejecutarse nuevamente porque es idempotente; después de confirmar que v1.7.1 está en verde, elimínalo.

## Importante
No uses todavía el importador real hasta que GitHub Actions de v1.7.1 termine en SUCCESS.
