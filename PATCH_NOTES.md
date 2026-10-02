# Impacto Nexora v1.11.0 — Bloque 4

## Objetivo
Convertir la ficha del cliente en el expediente manual central y hacer que la información importada desde el Excel operativo llegue a los mismos campos.

## Datos centralizados
- Negocio y contacto.
- Teléfonos y WhatsApp.
- Email principal, operativo y corporativo.
- Dirección USA, ciudad, estado, ZIP y zona horaria.
- Industria, servicios y fuente comercial.
- Website, Facebook, Instagram y plataformas.
- Días/horarios de atención.
- Experiencia y cobertura.
- Métodos de pago y política de estimados.
- Idiomas.
- Servicios a promocionar.
- Estado del logotipo y colores.
- Dominio, proveedores, hosting y fechas de renovación.
- Notas operativas.

## Información que NO se duplica
Plan actual, fechas del plan, beneficios, cortesías, inversiones, pagos y balance se leen de Contratos/Ventas/Pagos.

## Seguridad
No se agregó ningún campo de contraseña. El importador continúa excluyendo CLAVE, CONTRASEÑA DEL CORREO y la hoja CONTRASEÑAS.

## Migración
Esta versión SÍ requiere una migración única:

    python -m scripts.upgrade_nexora_1_11_0

La migración es idempotente y únicamente agrega campos al perfil operativo.

## Railway
1. Coloca temporalmente en Pre-Deploy:
   `python -m scripts.upgrade_nexora_1_11_0`
2. Sube v1.11.0 a GitHub.
3. Espera el deploy exitoso.
4. Después elimina el comando y deja Pre-Deploy vacío.
5. NO cambies Start Command.
6. NO cambies Dockerfile.
