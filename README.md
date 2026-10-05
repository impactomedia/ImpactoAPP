# Impacto Nexora – Sistema Integral

Impacto Nexora es la plataforma interna de Impacto Media Agency LLC para CRM,
clientes, ventas, cobros, operaciones, imprenta, finanzas, RR. HH., soporte,
reportes, seguridad, respaldos y control administrativo.

Versión de cierre del núcleo: **v1.23.0**

## Arquitectura productiva

- Código fuente: GitHub
- Aplicación: Railway
- Base de datos: MySQL en Railway
- Persistencia de uploads/backups lógicos: Railway Volume del servicio web
- Dominio/DNS: Hostinger
- Framework: Flask + SQLAlchemy
- Servidor WSGI: Gunicorn

Flujo de despliegue:

`GitHub -> GitHub Actions -> Railway`

Hostinger no despliega la aplicación; se utiliza para dominio/DNS.

## Instalación local

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m scripts.init_db
python run.py
```

Abre:

`http://127.0.0.1:5000`

El usuario inicial de desarrollo puede configurarse mediante las variables
ADMIN_NAME, ADMIN_EMAIL y ADMIN_PASSWORD. Cambia cualquier credencial inicial
antes de utilizar una instalación real.

## MySQL

Ejemplo local:

```env
DATABASE_URL=mysql+pymysql://usuario:clave@localhost:3306/impacto_manager
```

Luego:

```bash
python -m scripts.init_db
```

## Módulos incluidos

- Login, recuperación de contraseña, 2FA y seguridad de sesión.
- Usuarios, roles, permisos, alcances y auditoría.
- CRM / Seguimientos / Pipeline / Cotizaciones.
- Conversión Seguimiento -> Cliente sin perder historial.
- Clientes y ficha integral.
- Ventas, pagos, saldos, cuentas por cobrar y comisiones.
- Renovaciones y vencimientos.
- Proyectos, tareas, Kanban, onboarding y cambios.
- Imprenta, diseños, aprobaciones, tracking, envíos parciales y recepción.
- Finanzas, ingresos, egresos, cuentas por pagar y nómina.
- RR. HH., horarios, asistencia, incidencias, vacaciones y documentos.
- Soporte/tickets.
- Notificaciones.
- Dashboards y reportes.
- Configuración y catálogos.
- Centro de Datos e importación/exportación.
- Backups automáticos y restore protegido.
- Planificación.
- Auditoría de Flujos.
- Aceptación final.

## Producción

Antes de considerar una instancia productiva:

- SECRET_KEY debe ser larga y privada.
- DATABASE_URL debe apuntar al MySQL correcto.
- HTTPS debe estar activo.
- Los uploads deben persistir en un Railway Volume.
- Backups automáticos deben estar activos.
- Debe existir al menos un backup validado.
- Debe realizarse periódicamente un restore drill en una base separada.
- GitHub Actions debe estar en SUCCESS antes de desplegar.
- `/healthz` debe responder `{"status":"ok"}`.

## Paginación y rendimiento

Variables opcionales:

```env
LIST_PAGE_SIZE=25
LIST_MAX_PAGE_SIZE=100
DB_POOL_RECYCLE_SECONDS=1800
```

## Uploads

Variables:

```env
MAX_UPLOAD_MB=20
MAX_FILE_UPLOAD_MB=20
```

Los uploads se validan por extensión, tamaño y firma/contenido básico.

## Backups

Crear backup manual:

```bash
python -m scripts.backup
```

Validar backup:

```bash
python -m scripts.restore_backup /ruta/backup.zip
```

Restore de prueba:

```bash
RESTORE_DATABASE_URL=mysql+pymysql://.../base_temporal \
python -m scripts.restore_backup /ruta/backup.zip
```

El script se niega a restaurar si RESTORE_DATABASE_URL coincide con
DATABASE_URL.

## Healthcheck

```text
GET /healthz
```

200:
```json
{"status":"ok"}
```

503:
```json
{"status":"degraded"}
```

## Pruebas

```bash
python -m pytest -q
```

El repositorio incluye pruebas de:
- seguridad;
- roles y permisos;
- CRM;
- ventas;
- clientes;
- timeline;
- renovaciones;
- dashboards/reportes;
- notificaciones;
- catálogos;
- importación/exportación;
- backups;
- planificación;
- reglas críticas;
- hardening;
- aceptación final.

## Cierre de aceptación

En Nexora:

`Configuración -> Aceptación final`

La entrega se considera cerrada únicamente cuando:
- todas las validaciones automáticas están correctas;
- todos los criterios manuales están aprobados;
- no hay criterios rechazados;
- el reporte de aceptación ha sido exportado.

Documentación complementaria incluida:
- `DOCUMENTACION_TECNICA_NEXORA_v1.23.txt`
- `MANUAL_ADMINISTRACION_NEXORA_v1.23.txt`
- `PLAN_ACEPTACION_FINAL_NEXORA_v1.23.txt`

## Integraciones futuras

La especificación contempla una fase posterior opcional para:
- email corporativo;
- WhatsApp Business;
- telefonía/VoIP;
- calendarios;
- conciliación de pagos;
- almacenamiento cloud;
- firma electrónica;
- contabilidad externa.

Estas integraciones no forman parte del cierre del núcleo v1.23.0.
