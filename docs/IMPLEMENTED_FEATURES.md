# Funciones incluidas – Impacto Manager

## Núcleo y seguridad
- Login, logout, recuperación y cambio de contraseña.
- Bloqueo temporal por intentos fallidos.
- Roles y permisos configurables.
- Desactivación de usuarios sin perder historial.
- CSRF, hash de contraseñas, archivos con extensiones permitidas y variables de entorno.
- Auditoría de operaciones sensibles.

## CRM
- Prospectos/seguimientos con propietario comercial.
- Detección básica de duplicados.
- Pipeline Kanban y cambio de etapa.
- Interacciones y próximos seguimientos.
- Transferencia de propietario con historial.
- Cotizaciones con múltiples ítems y versiones base.
- Conversión de cotización a venta sin perder historial del cliente.

## Clientes
- Ficha operativa individual.
- Datos comerciales, contacto, redes, ubicación y notas.
- Varios colaboradores asignados al mismo cliente.
- Servicios/paquetes con estado independiente activo/inactivo/caducado.
- Línea de tiempo de ventas, pagos e interacciones.
- Archivos adjuntos.
- Importación/exportación CSV.

## Ventas, pagos, deducciones y renovaciones
- Ventas manuales o desde cotización.
- Múltiples ítems por venta.
- Pago inicial y pagos parciales posteriores.
- Saldo automático y cuenta por cobrar.
- Reversión de pagos con auditoría.
- Deducciones que pueden afectar o no la base comisionable.
- Renovaciones y vencimientos con estados y notas.

## Comisiones y planilla
- Reglas configurables por rango, porcentaje, monto fijo, paquete y mínimo.
- Disparador al vender, cobrar totalmente o proporcional al cobro.
- Estados estimada/generada/aprobada/pagada/anulada.
- Planilla por período con salario base + comisiones + bonos - deducciones.
- Ajustes antes del cierre y marcado de planilla pagada.

## Proyectos y tareas
- Creación de proyectos desde ventas.
- Estados operativos completos.
- Equipo/coordinador, progreso y fechas.
- Tareas lista/Kanban, prioridad, responsable, comentarios, checklist y tiempo.
- Plantillas de tareas por producto/servicio.
- Solicitudes de cambio del cliente.

## Imprenta
- Órdenes vinculadas a cliente/venta.
- Varios productos por orden.
- Versiones de diseño y comentarios.
- Aprobación de diseño separada de aprobación de envío.
- Envíos con tracking o entrega local.
- Envíos parciales y recepciones parciales.
- Detección de atrasos.
- Incidencias: no recibido, faltante, dañado, reimpresión/reenvío; resolución auditada.

## Finanzas
- Dashboard financiero interno.
- Ingresos, egresos y gastos recurrentes.
- Cuentas por cobrar, promesas de pago y estados.
- Cuentas por pagar y abonos.
- Comisiones y planilla.
- Reportes/exportación CSV.

## Recursos Humanos
- Expediente del colaborador.
- Cargos, áreas, supervisor, modalidad, compensación y saldo de vacaciones.
- Horarios y asignaciones temporales.
- Mi Jornada: entrada, breaks, almuerzo y salida.
- Panel de asistencia y correcciones auditadas.
- Vacaciones/permisos con aprobación/rechazo/cancelación.
- Movimientos y ajustes de vacaciones.
- Evaluaciones, capacitaciones y documentos internos.

## Soporte
- Tickets vinculados a clientes.
- Prioridad, canal, responsable y SLA interno.
- Estados, comentarios y resolución.

## Administración adicional
- Búsqueda global.
- Notificaciones y alertas de renovación.
- Catálogos configurables.
- Catálogo de servicios/paquetes.
- Auditoría.
- Backup básico para SQLite/MySQL.
- Docker y ejecución con Gunicorn.
