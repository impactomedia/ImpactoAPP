# Impacto APP 1.7.0 — Clientes V2 Fase 4

## Objetivo
Sustituir la carga manual de la hoja operativa por una importación controlada, segura y auditable.

## Flujo
1. Administración/Gerencia carga el `.xlsx`.
2. El sistema procesa únicamente `CLIENTES`.
3. `TAREAS` queda excluida para la fase de entregables.
4. `CONTRASEÑAS` nunca se procesa.
5. Las filas `CLAVE` y `CONTRASEÑA DEL CORREO` se eliminan antes de guardar la vista previa.
6. Se genera un lote saneado.
7. El usuario revisa coincidencias, asesor, plan y advertencias.
8. Solo después de confirmar se modifica la base de datos.

## Qué importa
- Datos base del cliente.
- Teléfonos, correo, dirección y website.
- Facebook e Instagram.
- Perfil operativo de Clientes V2.
- Google Business/Maps y plataformas adicionales.
- Responsable comercial cuando puede identificarse.
- Contratos únicamente cuando el mapeo y las fechas son confiables.

## Qué NO importa automáticamente
- Contraseñas.
- Hoja CONTRASEÑAS.
- Hoja TAREAS.
- Ventas.
- Pagos.
- Descuentos.
- Créditos entre planes.
- Inversiones secundarias.

La información financiera histórica sí queda saneada dentro del lote para revisión posterior.

## Protección contra duplicados
La conciliación compara correo, teléfono, nombre normalizado y nombre muy similar. Los casos ambiguos se omiten.

## Base de datos
Nueva tabla: `client_import_batches`

El XLSX original no se almacena.

## Railway
Pre-Deploy temporal:
`python -m scripts.upgrade_clients_v2_phase4`
