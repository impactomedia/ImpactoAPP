# Changelog

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
