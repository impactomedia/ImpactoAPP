# Impacto APP 1.6.0 — Clientes V2 Fase 3

## Qué incorpora

### 1. Mis clientes
Cada colaborador puede abrir una vista de clientes relevantes para su trabajo:
- Asesor: cartera propia + asignaciones operativas activas.
- Supervisor: clientes de su equipo + asignaciones activas del equipo.
- Producción: clientes con asignación operativa activa.
- Administración / Gerencia / Superadmin: vista global.

### 2. Equipo operativo histórico
Nueva tabla `client_team_assignments` con colaborador, rol, servicio/plan, proyecto, fechas, estado, responsable principal, quién asignó/finalizó, motivo y notas.

La estructura anterior `client_collaborators` continúa existiendo y se sincroniza como resumen de compatibilidad.

### 3. Centro de coordinación
Ruta: `/clients/<id>/coordination`

Incluye equipo activo/histórico, alertas, renovaciones, dominio/hosting y línea de tiempo unificada.

### 4. Alertas y recordatorios
Se consolidan renovaciones, vencimientos de servicios, dominio/hosting, cuotas/promesas para usuarios autorizados, tareas y tickets.

### 5. Seguridad
- Supervisores no pueden asignar colaboradores fuera de su equipo.
- Producción no obtiene acceso financiero por estar asignado al cliente.
- Ventas, pagos y cobranza solo aparecen en la línea de tiempo para perfiles autorizados.

## Base de datos
Crea una tabla nueva: `client_team_assignments`.

No elimina ni altera tablas existentes.

## Railway
Usar temporalmente en Pre-Deploy:

`python -m scripts.upgrade_clients_v2_phase3`

Después de un despliegue correcto, retirar el Pre-Deploy Command.
