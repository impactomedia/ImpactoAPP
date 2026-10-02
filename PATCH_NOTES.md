# Impacto Nexora v1.9.0 — Bloque 2

## Objetivo
Implementar el concepto real de servicio principal de Impacto Media:
un único plan vigente, precio fijo de catálogo y upgrades que sustituyen al plan anterior.

## Reglas implementadas
- Paquetes principales: categoría `paquete`.
- Precio: `ProductService.base_price`, USD.
- Un solo plan principal vigente.
- Upgrade: únicamente a un paquete de mayor precio.
- Crédito: pagos ya reconocidos del plan principal vigente.
- El saldo viejo deja de ser exigible y el nuevo saldo se calcula sobre el nuevo plan.
- El historial anterior no se elimina.
- Compras adicionales continúan en Nueva Venta y se desarrollarán a fondo en el Bloque 3.

## Base de datos
NO requiere migración.

## Railway
- Pre-Deploy: dejar vacío.
- Start Command: no cambiar.
- Dockerfile: no cambiar.

## Validación local
- Python compile: OK.
- Jinja parse: OK.
- La validación de integración definitiva se realizará con GitHub Actions al subir el parche.
