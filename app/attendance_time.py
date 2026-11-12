"""Fechas de asistencia en Managua (UTC-6), conservando UTC en la base de datos.

Las columnas AttendanceMark.marked_at usan datetime UTC sin tzinfo.
La conversión se hace únicamente en los límites de lectura y edición.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ATTENDANCE_ZONE_NAME = "America/Managua"
try:
    ATTENDANCE_ZONE = ZoneInfo(ATTENDANCE_ZONE_NAME)
except ZoneInfoNotFoundError:
    # Las imágenes Python slim pueden no incluir la base de zonas horarias.
    # Nicaragua utiliza UTC−6 sin cambio de horario de verano actualmente.
    ATTENDANCE_ZONE = timezone(timedelta(hours=-6), name=ATTENDANCE_ZONE_NAME)


def utc_now():
    """Instante UTC sin zona, compatible con los registros existentes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(utc_value):
    """Interpreta marcaciones históricas sin tzinfo como UTC."""
    if utc_value is None:
        return None
    if utc_value.tzinfo is None:
        utc_value = utc_value.replace(tzinfo=timezone.utc)
    return utc_value.astimezone(ATTENDANCE_ZONE)


def today_local():
    """Fecha de la jornada en Nicaragua, no la fecha UTC del servidor."""
    return to_local(utc_now()).date()


def utc_range_for_local_day(day):
    """Rango semiabierto UTC [inicio, siguiente día) para un día de Managua."""
    start = datetime.combine(day, time.min, ATTENDANCE_ZONE)
    end = datetime.combine(day + timedelta(days=1), time.min, ATTENDANCE_ZONE)
    return (
        start.astimezone(timezone.utc).replace(tzinfo=None),
        end.astimezone(timezone.utc).replace(tzinfo=None),
    )


def local_form_to_utc(local_datetime):
    """Convierte la fecha/hora de un control datetime-local a UTC sin zona."""
    if local_datetime.tzinfo is not None:
        raise ValueError("Se esperaba una fecha y hora local sin zona horaria.")
    return (
        local_datetime.replace(tzinfo=ATTENDANCE_ZONE)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
