import getpass
from app import create_app
from app.extensions import db
from app.models import User, Role

app = create_app()
with app.app_context():
    email = input("Correo del administrador: ").strip().lower()
    name = input("Nombre: ").strip() or "Administrador"
    password = getpass.getpass("Contraseña (mín. 8 caracteres): ")
    if len(password) < 8:
        raise SystemExit("La contraseña debe tener al menos 8 caracteres.")
    role = Role.query.filter_by(name="superadmin").first()
    if not role:
        raise SystemExit("Ejecuta primero: python scripts/init_db.py")
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(name=name, email=email, role=role, active=True)
        db.session.add(user)
    else:
        user.name = name
        user.role = role
        user.active = True
    user.set_password(password)
    db.session.commit()
    print("Administrador listo.")
