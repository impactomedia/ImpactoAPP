# Changelog

## 1.9.0 — Impacto Nexora · Bloque 2: Servicio principal y Upgrades

- Nuevo flujo independiente `Servicio principal / Upgrade` para separar el plan principal de las compras adicionales.
- Solo puede existir un servicio principal vigente por cliente.
- Los paquetes principales usan exclusivamente el `base_price` del catálogo en USD; el precio no se escribe manualmente al vender.
- Si el cliente ya tiene un plan principal activo, Nexora muestra únicamente paquetes de mayor precio.
- Un upgrade sustituye al plan anterior en lugar de sumar ambos planes como deuda.
- El dinero ya reconocido en el plan principal se acredita automáticamente contra el precio del nuevo plan.
- Los upgrades sucesivos conservan el crédito heredado más los nuevos pagos realizados.
- El plan anterior queda en el historial como inactivo y deja de generar saldo exigible.
- Las cuotas y cuenta por cobrar del plan reemplazado quedan canceladas, sin crear pagos ficticios.
- El nuevo plan genera su contrato principal, snapshot de beneficios/cortesías, proyecto operativo y tareas de onboarding.
- Los paquetes principales quedan excluidos de la venta genérica para impedir precios manuales.
- Las cotizaciones que incluyen paquetes usan el precio fijo del catálogo; su confirmación comercial se realiza desde Servicio principal / Upgrade.
- Configuración > Productos/Servicios ahora permite editar precio fijo, categoría, duración, modalidad, mantenimiento, área, renovación, tipo físico y beneficios.
- Catálogo comercial fijado a USD.
- Compatibilidad con contratos históricos: si no existe un `principal=True`, se toma el paquete activo más reciente como referencia.
- Nueva batería `tests/test_nexora_principal.py` para plan inicial, upgrade y crédito acumulado.
- No hay cambios de esquema ni migración de base de datos.

## 1.8.2 — Hotfix de pagos y pruebas de alcance

- Corregida la sincronización inmediata entre un pago recién creado y `sale.payments`, evitando que `amount_paid` y `balance` quedaran desactualizados al crear una venta con pago inicial.
- La corrección aplica a todos los flujos que usan `add_payment()`.
- Ajustada la prueba de permisos para representar por separado Seguimientos y Clientes conforme al ciclo comercial de Impacto Nexora.
- No hay cambios de esquema ni migración de base de datos.

## 1.8.1 — Impacto Nexora · Bloque 1: Seguimiento → Cliente + USA/USD

- Todo registro manual nuevo inicia como **Seguimiento** en CRM; `/clients/new` redirige al alta de seguimiento.
- Un Seguimiento pasa automáticamente a **Cliente Activo** al confirmar su primera compra desde Nueva Venta o al convertir una cotización en venta.
- Se conserva el mismo registro y, por tanto, su historial comercial previo.
- La conversión automática queda auditada con el número de venta que la originó.
- Las pantallas de Cliente redirigen los registros que aún son Seguimiento de vuelta a CRM.
- Nueva Venta puede seleccionar tanto Seguimientos como Clientes existentes.
- Se eliminan de la interfaz los botones de conversión manual; se reemplazan por **Registrar compra**.
- Los clientes y seguimientos manuales quedan fijados a **USA**.
- Ventas y cotizaciones quedan fijadas exclusivamente a **USD**, incluso si se manipula el formulario.
- La interfaz de CRM adopta la terminología **Seguimiento** en lugar de Prospecto para el flujo principal.
- No hay cambios de esquema ni migración de base de datos.
- Se amplían las pruebas de Sales V2 para conversión automática, USA, USD y bloqueo de creación directa de clientes.

## 1.8.0 — Sales V2 · Cierre de Nueva Venta

- Se completa el flujo de `/sales/new` para crear una venta con cliente, asesor, productos/servicios, descuentos, pago inicial y plan de pago.
- El selector de asesores muestra únicamente colaboradores comerciales activos con usuario activo.
- Los `product_id` recibidos se validan contra el catálogo activo para impedir productos deshabilitados o IDs manipulados.
- Nuevo descuento por unidad en cada ítem; se conservan precio de lista, descuento, precio final y total.
- El formulario ahora captura notas de venta, fecha/referencia del pago inicial y método de pago ampliado.
- Nuevo plan de pago con dos modalidades: vencimiento único o cuotas personalizadas.
- Las cuotas personalizadas deben sumar exactamente el saldo posterior al pago inicial.
- Las cuotas se crean en `ClientInstallment` con la línea base del pago inicial para evitar doble aplicación.
- La cuenta por cobrar toma como vencimiento la próxima cuota pendiente.
- El detalle de venta muestra plan de pagos, descuentos, notas, pagos y reversa controlada.
- `/sales/new` presenta un mensaje útil cuando no existen clientes disponibles en lugar de dejar un formulario inutilizable.
- Errores inesperados al crear una venta revierten la transacción completa y se registran en logs.
- No hay cambios de esquema ni migración de base de datos.
- Nueva batería `tests/test_sales_v2.py`.

## 1.7.1 — Corrección Clientes V2 · Fase 4

- Corregido el reconocimiento de `Google Business Profile / Maps` dentro del parser del Excel operativo.
- Cuando la ficha del Excel indica Google pendiente de verificación, ahora se crea correctamente `ClientPlatform(platform_key="google_business", status="pendiente")`.
- No hay cambios de esquema ni nueva migración.
- La corrección responde al único fallo detectado por GitHub Actions en la v1.7.0: 69 pruebas pasaron y 1 falló por ausencia de la plataforma Google.

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
