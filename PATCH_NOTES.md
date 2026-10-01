# Impacto APP 1.4.0 — Clientes V2 · Fase 1

Esta versión inicia la nueva ficha operativa de Clientes V2 utilizando como referencia el archivo operativo actual y la especificación funcional del sistema.

## Qué agrega

- Perfil operativo independiente por cliente.
- Plataformas adicionales sin contraseñas.
- Historial enriquecido de planes/paquetes.
- Pestañas de Compras, Servicios, Pagos, Operaciones, Equipo, Archivos e Historial dentro de una sola ficha.
- Operaciones dentro de la ficha conserva los filtros de acceso de proyectos y tareas por rol/asignación.
- Snapshots de modalidad, mantenimiento, beneficios y cortesías para no perder las condiciones históricas de cada plan.
- Backfill automático de contratos existentes durante la actualización.

## Seguridad de credenciales

Esta fase NO importa ni almacena las claves que aparecen en el Excel. Las credenciales requerirán una bóveda cifrada y un permiso independiente en una fase posterior.

## Base de datos

Esta versión crea tres tablas nuevas:

- `client_operational_profiles`
- `client_platforms`
- `client_contract_details`

No elimina ni renombra tablas actuales.

## Railway — importante

Antes de arrancar el deployment con la versión 1.4.0, agrega temporalmente este Pre-Deploy Command:

    python -m scripts.upgrade_clients_v2

El script es idempotente y puede ejecutarse más de una vez sin borrar datos. Cuando el deployment de 1.4.0 termine correctamente, puedes quitar el Pre-Deploy Command.

NO cambies el Start Command.

## Después de desplegar

GitHub Actions ejecutará la suite automáticamente. Revisa que `Impacto APP Tests` quede en verde.
