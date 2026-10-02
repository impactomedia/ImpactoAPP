# Impacto Nexora v1.8.2 — Hotfix

Corrige los fallos de CI detectados en v1.8.0 y v1.8.1.

- `app/services.py`: sincroniza inmediatamente el pago con la relación ORM de la venta.
- `tests/test_roles_permissions.py`: separa Seguimiento y Cliente en la prueba de alcance.
- Sin migración de base de datos.
- Railway: Pre-Deploy vacío; no cambiar Start Command.
