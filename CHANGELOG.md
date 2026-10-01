# Changelog

## 1.7.0 — Clientes V2 · Fase 4

- Nueva importación controlada del Excel operativo con flujo Vista previa → Confirmación → Ejecución.
- El importador procesa únicamente la hoja `CLIENTES`.
- La hoja `CONTRASEÑAS` nunca se lee ni se almacena.
- Las filas `CLAVE` y `CONTRASEÑA DEL CORREO` de la hoja CLIENTES se eliminan antes de guardar el lote saneado.
- La hoja `TAREAS` queda excluida para la futura fase de entregables.
- Nueva conciliación de clientes existentes por correo, teléfono, nombre normalizado y nombre muy similar para reducir duplicados.
- Los casos ambiguos se omiten automáticamente en lugar de crear un registro duplicado.
- Importación de datos base, perfil operativo, Facebook/Instagram, Google Business/Maps y plataformas adicionales.
- Resolución de responsable comercial por nombre y aliases operativos, sin reemplazar propietarios existentes salvo autorización explícita.
- Los datos existentes no se sobrescriben por defecto; la importación completa campos vacíos.
- Contratos automáticos únicamente cuando producto, fecha y estructura de inversiones tienen alta confianza.
- Ventas, pagos, descuentos, créditos e inversiones múltiples no se crean automáticamente; permanecen en el lote saneado para revisión.
- Nueva tabla `client_import_batches` para trazabilidad de vistas previas y resultados sin conservar el XLSX original.
- Nueva interfaz `Clientes > Importar Excel Operativo`, restringida a Superadministración, Administración y Gerencia.
- Script idempotente `python -m scripts.upgrade_clients_v2_phase4`.
- Nueva batería `tests/test_clients_v2_phase4.py`.
- El parser fue validado con el Excel operativo actual: 9 fichas detectadas y 12 campos sensibles omitidos.

## 1.6.0 — Clientes V2 · Fase 3

- Nuevo Centro de coordinación por cliente con equipo operativo, alertas/renovaciones y línea de tiempo unificada.
- Nueva sección “Mis clientes” según cartera comercial y asignaciones operativas activas.
- Historial de equipo por cliente/servicio/proyecto con fecha de inicio, suspensión, reactivación, finalización y motivo.
- Las asignaciones anteriores de `ClientCollaborator` se migran a historial V3 y permanecen sincronizadas para conservar compatibilidad con la pestaña Equipo existente.
- Supervisores solo pueden asignar colaboradores dentro de su propio equipo; asesores no reciben permisos nuevos para modificar equipo.
- Producción ve en “Mis clientes” únicamente clientes donde tenga asignación operativa activa.
- Alertas consolidadas para renovaciones, vencimientos de servicios, dominio, hosting, cuotas/promesas de pago, tareas y tickets, respetando permisos.
- Recordatorios de renovación se extienden al equipo operativo activo sin exponer información financiera.
- Línea de tiempo unificada reúne interacciones, comentarios, servicios, equipo, cambios de responsable, proyectos, tareas, tickets, archivos y renovaciones; ventas/pagos/cobranza solo aparecen para perfiles con permiso financiero/comercial.
- Dashboard actualizado con acceso rápido y contador de “Mis clientes”.
- Script idempotente `python -m scripts.upgrade_clients_v2_phase3`.
- Nueva batería `tests/test_clients_v2_phase3.py`.

## 1.5.0 — Clientes V2 · Fase 2

- Nuevo plan de cuotas por venta dentro de la ficha del cliente, con monto, vencimiento, saldo aplicado y estado automático.
- Los pagos confirmados se distribuyen automáticamente entre las cuotas programadas y las reversas recalculan el plan sin duplicar movimientos.
- Las cuotas creadas después de un pago inicial conservan una línea base para no reaplicar dinero cobrado anteriormente.
- Nuevo historial de cobranza con notas, acuerdos y promesas de pago vinculables a una venta/cuenta por cobrar.
- Las promesas pueden marcarse como cumplidas, incumplidas o canceladas y actualizan la cuenta por cobrar.
- Nuevo estado de cuenta imprimible desde la ficha con ventas, pagos confirmados, cuotas y seguimientos de cobranza.
- Documentos del cliente categorizados: administrativo, contratos, branding, website, redes, SEO/Google, imprenta, comprobantes y otros.
- Los archivos de cliente dejan de abrirse mediante URL directa de `static/uploads/client_*`; se descargan mediante una ruta que valida acceso a la ficha.
- Metadatos editables para documentos y opción de eliminar archivos desde la ficha.
- Script MySQL-safe e idempotente `python -m scripts.upgrade_clients_v2_phase2` para crear las tablas nuevas y categorizar archivos históricos como “Otros”.
- Nueva batería `tests/test_clients_v2_phase2.py` para cuotas, asignación automática de pagos, promesas, documentos y permisos.

## 1.4.0 — Clientes V2 · Fase 1

- Nueva ficha operativa de cliente organizada por pestañas: Resumen, Datos, Compras, Servicios, Pagos, Operaciones, Plataformas, Equipo, Archivos e Historial.
- La pestaña Operaciones respeta el alcance individual de proyectos y tareas para evitar mostrar trabajo no asignado a perfiles restringidos.
- Nuevo perfil operativo por cliente: días y horarios de atención, experiencia, cobertura, métodos de pago, política de estimados, correos operativos/corporativos, servicios a promocionar, estado del logotipo y colores de marca.
- Fechas estructuradas de activación/renovación de dominio y hosting, más notas operativas.
- Nueva tabla de plataformas adicionales para Google Business/Maps, YouTube, TikTok, Vimeo, Pinterest, LinkedIn y directorios, sin guardar contraseñas.
- Los paquetes/servicios conservan snapshots de modalidad, mantenimiento, beneficios, cortesías y motivo de estado mediante una tabla de detalle histórico.
- Las ventas existentes continúan siendo la fuente única para 1.ª, 2.ª, 3.ª y futuras inversiones; no se duplican montos en una tabla paralela.
- El flujo automático de Ventas genera el detalle V2 de cada nuevo contrato.
- Script MySQL-safe e idempotente `python -m scripts.upgrade_clients_v2` para crear las nuevas tablas y completar detalles históricos de contratos existentes.
- Nuevas pruebas de permisos, perfil operativo, plataformas y snapshots de contratos.
- No se migran contraseñas del Excel en texto plano; la bóveda de credenciales queda reservada para una fase segura independiente.

## 1.3.2 — Cierre de auditoría automatizada

- Las páginas 403 y 404 ya funcionan correctamente también para visitantes sin sesión iniciada.
- Los archivos protegidos bajo `/static/uploads/` pueden devolver 404 sin provocar errores de plantilla.
- El Kanban queda restringido a Superadministrador, Administración, Gerencia y Supervisor/Coordinación.
- Asesores, Producción, RR. HH. y Finanzas continúan trabajando con las listas de tareas filtradas según su alcance.
- Ajuste realizado a partir de la ejecución real de GitHub Actions: 41 pruebas ya pasaban antes de este cierre.

## 1.3.1 — Corrección de CI

- GitHub Actions ejecuta las pruebas con `python -m pytest -q`.
- Se define `PYTHONPATH=.` para que el paquete `app` se importe correctamente.

## 1.3.0 — Auditoría integral y endurecimiento final

- Seguridad de autenticación: redirecciones internas seguras, invalidación de tokens de recuperación y contraseña actual obligatoria para cambios desde el perfil.
- Mejor manejo de fallos SMTP en recuperación de contraseña sin revelar existencia de cuentas.
- Soporte correcto de proxy inverso para Railway mediante `ProxyFix` y encabezados HTTP básicos de seguridad.
- Los archivos de `static/uploads` dejan de ser accesibles para visitantes no autenticados.
- CRM endurecido: validación de responsables por cartera/equipo, protección contra reasignaciones no autorizadas, validación de presupuestos, prioridades, interacciones y duplicados.
- Cotizaciones: validación estricta de productos, cantidades, precios y descuentos; una cotización en borrador ya no mueve el prospecto a “Cotización enviada”.
- Soporte: validación de responsables activos en creación y actualización de tickets.
- Configuración: validaciones de roles, catálogos, productos y valores del sistema; duplicados de catálogo ya no provocan errores 500.
- Roles: los permisos inherentes de Superadministrador se muestran como no editables para evitar configuraciones engañosas.
- Reportes: las exportaciones CSV respetan el período seleccionado y generan UTF-8 con encabezados más claros.
- Correcciones visuales de capitalización y etiquetas en CRM, Clientes, RR. HH., Ventas y Reportes.
- Capacitaciones: estados de interfaz alineados con los estados realmente aceptados por el backend.
- Pruebas nuevas de seguridad, CRM, soporte, reportes, configuración y CSRF.
- GitHub Actions agregado para ejecutar `pytest -q` automáticamente en cada push a `main` y pull request.

## 1.2.2 — Auditoría funcional

- Reglas de negocio reforzadas en ventas, pagos, comisiones, operaciones, imprenta, RR. HH., finanzas y reportes.
- Protección contra sobrepagos, doble aprobación de vacaciones y comisiones fuera del período de planilla.
- Alcance de datos más estricto para asesores, supervisores y producción.

## 1.2.1 — Alcance por cartera y equipo

- Clientes, ventas, proyectos, imprenta y soporte limitados según cartera/equipo cuando corresponde.
- Controles visuales alineados con permisos reales.

## 1.2.0 — Roles y permisos

- Capa central de autorización por módulo/acción.
- Matriz de permisos para perfiles predefinidos.
- Páginas 403/404 y mejoras de presentación de estados.

## 1.0.0-complete

Entrega integral inicial de Impacto Manager: CRM, clientes, ventas/pagos, operaciones, imprenta, finanzas, comisiones/planilla, RR. HH., soporte, reportes, configuración, auditoría y despliegue base.
