# Impacto Nexora v1.12.0 — Bloque 5

## Objetivo
Convertir la pestaña Historial del expediente del cliente en una línea de tiempo automática que una lo que ya ocurre en CRM, Ventas, Desarrollo, Imprenta, Soporte, Archivos y Renovaciones.

## Fuentes del Timeline
- Interacciones CRM.
- Cotizaciones.
- Servicio principal y contratos.
- Ventas, upgrades y compras adicionales.
- Pagos y cobranza.
- Comentarios internos.
- Proyectos.
- Tareas y comentarios de tareas.
- Solicitudes de cambio.
- Tickets y comentarios de soporte.
- Órdenes de imprenta.
- Versiones de diseño.
- Envíos y recepciones.
- Incidencias de imprenta.
- Archivos.
- Renovaciones.
- Transferencias de responsable.
- Cambios de estado históricos desde AuditLog.

## Permisos
El Timeline respeta los permisos ya existentes:
- información financiera solo para roles con Sales/Finance;
- CRM solo para roles con CRM;
- proyectos/tareas solo dentro del alcance permitido;
- imprenta y soporte según sus permisos.

## Base de datos
NO requiere migración.

## Railway
- Pre-Deploy: vacío.
- Start Command: no cambiar.
- Dockerfile: no cambiar.
