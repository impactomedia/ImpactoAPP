# Impacto Manager – Sistema Integral

Aplicación web para Impacto Media Agency construida con Flask + SQLAlchemy + MySQL/SQLite. Incluye módulos de CRM, clientes, ventas/pagos, proyectos/tareas, imprenta, finanzas/comisiones/planilla, RR. HH., soporte, reportes, configuración y auditoría.

## 1. Instalación local

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m scripts.init_db
python run.py
```

Abre `http://127.0.0.1:5000`.

Usuario inicial por defecto: `admin@impactomedia.local` / `ChangeMe123!` (cámbialo inmediatamente). Puedes configurar otro en `.env` antes de inicializar.

## 2. MySQL

Crea una base y edita `.env`:

```env
DATABASE_URL=mysql+pymysql://usuario:clave@localhost:3306/impacto_manager
```

Luego ejecuta `python -m scripts.init_db`.

## 3. Docker

```bash
docker compose up --build
```

## 4. Módulos incluidos

- Usuarios, roles, permisos y recuperación de contraseña.
- CRM de prospectos, pipeline, interacciones, propiedad comercial y cotizaciones.
- Conversión sin perder historial de seguimiento → cliente.
- Ficha operativa de cliente, colaboradores asignados, contratos/paquetes, timeline, importación/exportación CSV.
- Ventas, pagos parciales, saldos, cuentas por cobrar, deducciones y comisiones configurables.
- Proyectos generados desde ventas, tareas, Kanban, comentarios y solicitudes de cambio.
- Imprenta: orden, versiones de diseño, aprobación de diseño separada de aprobación de envío, producción, tracking, envíos parciales, recepción e incidencias.
- Finanzas: ingresos, egresos, cuentas por cobrar/pagar, reglas de comisión y planilla.
- Recursos Humanos: expediente, horarios, marcaciones, breaks/almuerzo, vacaciones/permisos, evaluaciones, capacitaciones y documentos.
- Soporte/tickets con prioridad y SLA interno.
- Renovaciones y alertas.
- Reportes y exportaciones.
- Catálogos/configuraciones y bitácora de auditoría.
- Script de backup básico (`python -m scripts.backup`).

## 5. Valores que deben configurarse con la empresa

Las políticas exactas de asistencia, vacaciones, comisiones, precios y reglas internas se dejaron configurables porque deben definirse con Impacto Media Agency. No se deben liquidar comisiones o vacaciones con valores de ejemplo sin validarlos.

## 6. Flask-Migrate para cambios futuros

La instalación inicial usa `db.create_all()` para poder arrancar en una base nueva. A partir de la primera versión en producción, inicializa migraciones:

```bash
python -m flask --app run db init
python -m flask --app run db migrate -m "baseline"
python -m flask --app run db upgrade
```

Mantén backups antes de cada migración.

## 7. Producción

- Usa una `SECRET_KEY` larga y aleatoria.
- Activa HTTPS en proxy/hosting.
- Cambia la contraseña inicial.
- Configura SMTP para recuperación de contraseña.
- Configura backups automáticos y prueba restauración.
- No expongas `.env` ni credenciales en Git.

## 8. Pruebas rápidas

Con las dependencias instaladas:

```bash
pytest -q
```

El smoke test inicia una base SQLite en memoria, crea catálogos/roles, inicia sesión con el superadministrador y verifica las páginas principales.
