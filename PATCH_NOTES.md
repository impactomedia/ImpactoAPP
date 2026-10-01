# Impacto APP 1.3.0 — Auditoría integral final

Este paquete contiene **archivos completos de reemplazo**, no fragmentos.

## Cómo aplicarlo

1. Descomprime el ZIP.
2. Abre la carpeta `ImpactoAPP_Auditoria_Final_1_3_0`.
3. Copia **todo su contenido** sobre la raíz de tu proyecto `ImpactoAPP`.
4. Acepta reemplazar los archivos existentes.
5. Sube los cambios a GitHub.
6. Railway puede desplegar normalmente el último commit.

## Railway

- No agrega tablas ni columnas.
- No requiere migración de MySQL.
- No requiere `Pre-Deploy Command`.
- No cambies el `Start Command`.

## GitHub Actions

Se agrega `.github/workflows/tests.yml`. Al subir este parche a GitHub, la suite de pruebas se ejecutará automáticamente. En GitHub podrás verla en la pestaña **Actions**.

## Correcciones principales

- Autenticación y recuperación de contraseña más seguras.
- Bloqueo de redirecciones externas desde el login.
- CRM con asignación de responsable limitada al alcance real del usuario.
- Cotizaciones con validaciones completas y corrección del estado “borrador”.
- Tickets con validación de responsables activos.
- Roles, catálogos, productos y configuraciones con validaciones para evitar errores 500.
- Reportes CSV limitados al período seleccionado.
- Capitalización y estados visuales corregidos.
- Estados de capacitaciones alineados entre frontend/backend.
- Archivos subidos no disponibles para usuarios anónimos.
- Encabezados básicos de seguridad HTTP.
- Pruebas automatizadas nuevas y CI en GitHub.

## Archivos incluidos

- `app/__init__.py`
- `app/blueprints/auth.py`
- `app/blueprints/crm.py`
- `app/blueprints/reports.py`
- `app/blueprints/settings.py`
- `app/blueprints/support.py`
- `app/templates/auth/profile.html`
- `app/templates/clients/index.html`
- `app/templates/crm/pipeline.html`
- `app/templates/crm/prospect_form.html`
- `app/templates/crm/prospects.html`
- `app/templates/crm/quote_detail.html`
- `app/templates/crm/quote_form.html`
- `app/templates/hr/trainings.html`
- `app/templates/reports/index.html`
- `app/templates/sales/index.html`
- `app/templates/sales/renewals.html`
- `app/templates/settings/roles.html`
- `tests/test_audit_final.py`
- `.github/workflows/tests.yml`
- `VERSION`
- `CHANGELOG.md`
- `PATCH_NOTES.md`

## Importante

Este parche parte de la versión `1.2.2` que ya tienes en `main`. No reemplaza las correcciones de 1.2.2: las complementa.
