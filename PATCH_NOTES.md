# Impacto APP 1.8.0 — Sales V2

## Qué se corrige
La pantalla Nueva venta existía, pero no cubría completamente el flujo definido para Impacto APP.

## Nuevo flujo
Cliente
→ asesor
→ productos/servicios
→ descuentos
→ total
→ pago inicial
→ saldo
→ vencimiento único o cuotas personalizadas
→ cuenta por cobrar
→ generación de contrato/proyecto/tareas/imprenta según producto

## Validaciones
- Cliente debe estar dentro del alcance del usuario.
- Asesor debe ser comercial y estar activo.
- Producto debe estar activo.
- Cantidad > 0.
- Precio >= 0.
- Descuento >= 0 y no puede superar precio lista.
- Pago inicial no puede superar total.
- Si queda saldo con vencimiento único, la fecha es obligatoria.
- Si se usan cuotas, su suma debe coincidir exactamente con el saldo.
- Ninguna cuota puede vencer antes de la fecha de venta.

## Base de datos
No hay nuevas tablas.
No hay migración.

## Railway
No agregues ningún Pre-Deploy Command para esta versión.
