"""Actualiza permisos y etiquetas de los roles predefinidos sin borrar datos.

Ejecutar una sola vez después de aplicar este parche:
    python -m scripts.upgrade_role_permissions
"""
from app import create_app
from scripts.seed import seed_all


app = create_app()

with app.app_context():
    seed_all()
    print("Permisos, etiquetas y roles actualizados correctamente.")
