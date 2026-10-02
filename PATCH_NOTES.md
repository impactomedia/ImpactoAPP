# Impacto Nexora v1.10.0 — Bloque 3

## Qué implementa
- Compra adicional separada del servicio principal.
- Contado.
- Pago inicial + saldo en entrega.
- Cuotas.
- Financiamiento personalizado.
- Metadata comercial por operación.
- Total invertido / saldo pendiente / servicio actual / compras adicionales.

## Migración
Esta versión SÍ requiere una migración única:

    python -m scripts.upgrade_nexora_1_10_0

La migración:
- crea `sale_operation_meta`;
- no elimina ventas ni pagos;
- clasifica operaciones históricas;
- es idempotente.

## Railway
1. Coloca temporalmente en Pre-Deploy:
   `python -m scripts.upgrade_nexora_1_10_0`
2. Despliega.
3. Cuando el deploy termine correctamente, elimina ese comando y deja Pre-Deploy vacío.
4. NO cambies Start Command.
5. NO cambies Dockerfile.
