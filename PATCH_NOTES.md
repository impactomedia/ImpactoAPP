# Impacto Nexora 1.8.1 — Bloque 1

## Objetivo
Alinear el sistema con la regla comercial real de Impacto Media:

**Seguimiento → compra confirmada → Cliente Activo**

## Cambios principales
- Alta manual únicamente como Seguimiento.
- La venta es el evento que convierte el registro en Cliente.
- La conversión conserva el mismo `Client.id` y todo el historial de CRM.
- Ventas y cotizaciones nuevas usan únicamente USD.
- Los registros manuales se fijan a USA.
- Los botones de “Convertir a cliente” del CRM se reemplazan por “Registrar compra”.
- Nueva Venta lista Seguimientos y Clientes y puede abrirse preseleccionando un seguimiento desde CRM.

## Compatibilidad
- No borra ni migra clientes existentes.
- No modifica ventas ni pagos históricos.
- No cambia el esquema de la base de datos.
- Los clientes importados previamente continúan funcionando.

## Railway
No agregues ningún comando a **Pre-Deploy** ni a **Start Command** para esta versión.
El Dockerfile actual debe seguir iniciando Gunicorn normalmente.
