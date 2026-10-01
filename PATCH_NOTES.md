# Impacto APP 1.5.0 — Clientes V2 Fase 2

## Incluye

- Plan de cuotas por venta.
- Aplicación automática de pagos confirmados a cuotas.
- Recalculo de cuotas cuando un pago se reversa.
- Historial de cobranza y promesas de pago.
- Estado de cuenta imprimible.
- Documentos categorizados con descarga protegida.
- Metadatos de documentos y eliminación controlada.

## Base de datos

Esta versión crea tres tablas nuevas:

- `client_installments`
- `client_collection_notes`
- `client_document_meta`

No elimina ni renombra tablas existentes.

## Railway

Usar temporalmente como Pre-Deploy Command:

```bash
python -m scripts.upgrade_clients_v2_phase2
```

Después de un deployment exitoso y pruebas verdes, eliminar nuevamente el Pre-Deploy Command.
No modificar Start Command.
