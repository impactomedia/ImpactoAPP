# Impacto APP 1.2.2 — Auditoría funcional

Este parche se aplica reemplazando los archivos incluidos sobre la carpeta del proyecto.

## Importante

- No agrega tablas ni columnas.
- No requiere migración de MySQL.
- No requiere Pre-Deploy Command en Railway.
- Después de subir a GitHub, Railway puede desplegar normalmente.
- `VERSION` y `CHANGELOG.md` están en la raíz del parche; cópialos también.

## Validaciones recomendadas después del despliegue

1. Iniciar sesión como Superadministrador y comprobar todos los módulos.
2. Asesor: confirmar que solo ve su cartera, ventas, renovaciones y proyectos relacionados.
3. Supervisor: confirmar que ve únicamente su cartera/equipo en Comercial y RR. HH.
4. Producción: confirmar Kanban y proyectos asignados, sin indicadores financieros en Reportes.
5. RR. HH.: confirmar marcaciones, vacaciones y nómina sin botón para marcar planilla pagada.
6. Finanzas: registrar un pago y comprobar que no acepta montos superiores al saldo.
7. Imprenta: comprobar que no permite despachar cantidades en cero ni productos sin aprobación de envío.
