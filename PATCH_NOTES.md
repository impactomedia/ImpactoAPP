# Impacto APP 1.3.2 — Cierre de auditoría

Este parche corrige los dos fallos detectados por la ejecución real de GitHub Actions en la versión 1.3.1.

## Correcciones

1. Páginas 403/404 públicas
   - Ya no dependen de `app_base.html`.
   - Funcionan correctamente para usuarios anónimos.
   - Corrige el fallo al bloquear acceso público a `/static/uploads/`.

2. Kanban
   - Se restringe a roles de coordinación/administración:
     - Superadministrador
     - Administración
     - Gerencia
     - Supervisor / Coordinador
   - Los demás perfiles mantienen sus listas de tareas filtradas.

## Despliegue

- No modifica MySQL.
- No requiere migración.
- No requiere Pre-Deploy Command.
- Copia todos los archivos sobre tu proyecto y sube a GitHub.
- GitHub Actions se ejecutará automáticamente.
