from functools import wraps
from flask import abort
from flask_login import current_user


def roles_required(*roles):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.is_superadmin:
                return func(*args, **kwargs)
            role_name = current_user.role.name if current_user.role else ""
            if roles and role_name not in roles:
                abort(403)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def permission_required(code):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_permission(code):
                abort(403)
            return func(*args, **kwargs)
        return wrapper
    return decorator
