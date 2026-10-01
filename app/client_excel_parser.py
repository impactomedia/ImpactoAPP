from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import PurePosixPath
import hashlib
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile


MAX_WORKBOOK_BYTES = 12 * 1024 * 1024
MAX_XML_PART_BYTES = 25 * 1024 * 1024
MAX_CLIENT_ROWS = 250
MAX_CLIENT_COLS = 120

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

_SECRET_LABELS = ("clave", "contrasena", "contraseña", "password", "passcode", "secret")
_PLATFORM_LABELS = {
    "google_business": ("google business", "google", "maps"),
    "facebook": ("facebook", "fb"),
    "instagram": ("instagram",),
    "youtube": ("youtube", "you tube"),
    "tiktok": ("tik tok", "tiktok"),
    "vimeo": ("vimeo",),
    "pinterest": ("pinterest",),
    "linkedin": ("linkedin",),
    "manta": ("manta",),
    "houzz": ("houzz",),
    "porch": ("porch",),
    "buildzoom": ("buildzoom", "build zoom"),
    "merchantcircle": ("merchantcircle", "merchant circle"),
    "mapquest": ("mapquest", "map quest"),
    "yelp": ("yelp",),
    "yellow_pages": ("yellow pages", "yellowpages"),
}
_MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


class WorkbookImportError(ValueError):
    pass


def normalize_text(value):
    text = str(value or "").strip().lower()
    text = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    return " ".join(text.split())


def _clean_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _scrub_sensitive_text(value):
    text = _clean_text(value)
    if not text:
        return ""
    patterns = [
        r"(?im)^\s*(?:clave|contrase(?:n|ñ)a|password|passcode|secret)\s*[:=\-].*$",
        r"(?im)\b(?:clave|contrase(?:n|ñ)a|password|passcode|secret)\s*[:=\-]\s*\S+",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "[DATO SENSIBLE OMITIDO]", text)
    return text.strip()


def _looks_sensitive_label(label):
    norm = normalize_text(label)
    return any(token in norm for token in _SECRET_LABELS)


def _safe_part(zf, name):
    try:
        info = zf.getinfo(name)
    except KeyError as exc:
        raise WorkbookImportError(f"El archivo XLSX no contiene {name}.") from exc
    if info.file_size > MAX_XML_PART_BYTES:
        raise WorkbookImportError("El archivo XLSX contiene una sección demasiado grande.")
    return zf.read(name)


def _shared_strings(zf):
    try:
        raw = _safe_part(zf, "xl/sharedStrings.xml")
    except WorkbookImportError:
        return []
    root = ET.fromstring(raw)
    out = []
    for si in root.findall(f"{{{_NS_MAIN}}}si"):
        pieces = []
        for node in si.iter(f"{{{_NS_MAIN}}}t"):
            pieces.append(node.text or "")
        out.append("".join(pieces))
    return out


def _sheet_targets(zf):
    workbook = ET.fromstring(_safe_part(zf, "xl/workbook.xml"))
    rels = ET.fromstring(_safe_part(zf, "xl/_rels/workbook.xml.rels"))
    rel_map = {}
    for rel in rels.findall(f"{{{_NS_PKG_REL}}}Relationship"):
        rel_map[rel.attrib.get("Id")] = rel.attrib.get("Target", "")

    sheets = {}
    sheets_node = workbook.find(f"{{{_NS_MAIN}}}sheets")
    if sheets_node is None:
        return sheets

    for sheet in sheets_node.findall(f"{{{_NS_MAIN}}}sheet"):
        name = sheet.attrib.get("name", "")
        rid = sheet.attrib.get(f"{{{_NS_REL}}}id")
        target = rel_map.get(rid, "")
        if not target:
            continue
        target = target.replace("\\", "/")
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = str(PurePosixPath("xl") / target)
        sheets[name] = target
    return sheets


def _column_index(cell_ref):
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        return None
    idx = 0
    for ch in match.group(1):
        idx = idx * 26 + (ord(ch) - 64)
    return idx


def _cell_value(cell, shared):
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        pieces = [node.text or "" for node in cell.iter(f"{{{_NS_MAIN}}}t")]
        return "".join(pieces)

    v = cell.find(f"{{{_NS_MAIN}}}v")
    if v is None or v.text is None:
        return None

    raw = v.text
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if cell_type == "b":
        return raw == "1"
    if cell_type in {"str", "e"}:
        return raw

    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw


def _read_sheet_cells(zf, sheet_path, shared):
    raw = _safe_part(zf, sheet_path)
    root = ET.fromstring(raw)
    cells = {}
    for cell in root.iter(f"{{{_NS_MAIN}}}c"):
        ref = cell.attrib.get("r")
        if not ref:
            continue
        match = re.match(r"([A-Z]+)(\d+)", ref)
        if not match:
            continue
        row = int(match.group(2))
        col = _column_index(ref)
        if row > MAX_CLIENT_ROWS or not col or col > MAX_CLIENT_COLS:
            continue
        cells[(row, col)] = _cell_value(cell, shared)
    return cells


def parse_date_value(value):
    """Devuelve (fecha, precisión): exact, approximate o none."""
    if value in (None, ""):
        return None, "none"

    if isinstance(value, (int, float)):
        number = float(value)
        if 20000 <= number <= 70000:
            return (datetime(1899, 12, 30) + timedelta(days=number)).date(), "exact"
        return None, "none"

    raw = _clean_text(value)
    if not raw or normalize_text(raw) in {"n/a", "na", "no aplica", "no tiene", "pendiente"}:
        return None, "none"

    match = re.search(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?!\d)", raw)
    if match:
        day, month, year = map(int, match.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day), "exact"
        except ValueError:
            pass

    norm = normalize_text(raw).replace(".", " ")
    for month_name, month in _MONTHS_ES.items():
        match = re.search(rf"\b{month_name}\b\s*(\d{{4}})", norm)
        if match:
            return date(int(match.group(1)), month, 1), "approximate"

    match = re.search(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", raw)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return date(year, month, day), "exact"
        except ValueError:
            pass

    return None, "none"


def _extract_domain_dates(value):
    raw = _clean_text(value)
    result = {"domain_activated_on": None, "hosting_activated_on": None}
    if not raw:
        return result

    for key, token in [
        ("domain_activated_on", "dominio"),
        ("hosting_activated_on", "hosting"),
    ]:
        match = re.search(
            rf"{token}\s*[:\-]?\s*(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}})",
            raw,
            flags=re.I,
        )
        if match:
            parsed, _ = parse_date_value(match.group(1))
            result[key] = parsed.isoformat() if parsed else None

    if not result["domain_activated_on"] and not result["hosting_activated_on"]:
        parsed, precision = parse_date_value(raw)
        if parsed:
            result["domain_activated_on"] = parsed.isoformat()
            result["domain_date_precision"] = precision
    return result


def _clean_url(value):
    raw = _clean_text(value)
    if not raw:
        return None
    norm = normalize_text(raw)
    if norm in {"n/a", "na", "no tiene", "no", "pendiente"}:
        return None
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw.lstrip("/")
    return raw[:500]


def _platform_key(label):
    norm = normalize_text(label)
    for key, aliases in _PLATFORM_LABELS.items():
        if any(alias in norm for alias in aliases):
            return key
    return None


def _platform_payload(label, value):
    key = _platform_key(label)
    if not key:
        return None

    raw = _clean_text(value)
    if not raw:
        return None
    norm = normalize_text(raw)
    if norm in {"n/a", "na", "no tiene", "no"}:
        return {
            "key": key,
            "label": label,
            "url": None,
            "status": "no_aplica",
            "notes": raw,
        }
    if re.match(r"^https?://", raw, re.I):
        return {
            "key": key,
            "label": label,
            "url": raw[:500],
            "status": "activo",
            "notes": None,
        }
    if "pendiente" in norm or "verific" in norm:
        return {
            "key": key,
            "label": label,
            "url": None,
            "status": "pendiente",
            "notes": _scrub_sensitive_text(raw),
        }
    return {
        "key": key,
        "label": label,
        "url": None,
        "status": "pendiente",
        "notes": _scrub_sensitive_text(raw),
    }


def _coverage_text(value):
    raw = _clean_text(value)
    if not raw:
        return ""
    if isinstance(value, (int, float)) or re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return f"{raw} millas"
    return raw


def _benefit_text(*values):
    parts = []
    for value in values:
        raw = _scrub_sensitive_text(value)
        if not raw:
            continue
        norm = normalize_text(raw)
        if norm.startswith("beneficios") or norm == "cortesia":
            continue
        parts.append(raw)
    return "\n".join(parts).strip()


def _investment_rows(cells, label_col, value_col):
    rows = []
    multiple = False
    for row in range(47, 78):
        label = _clean_text(cells.get((row, label_col)))
        value = _clean_text(cells.get((row, value_col)))
        extra = _clean_text(cells.get((row, value_col + 1)))
        if not label and not value and not extra:
            continue
        if _looks_sensitive_label(label):
            continue

        safe_label = _scrub_sensitive_text(label)
        safe_value = _scrub_sensitive_text(value)
        safe_extra = _scrub_sensitive_text(extra)

        if re.search(
            r"\b(?:2da|3ra|4ta|segunda|tercera|inversiones secundarias)\b",
            normalize_text(label),
        ):
            multiple = True

        rows.append(
            {
                "row": row,
                "label": safe_label,
                "value": safe_value,
                "extra": safe_extra,
            }
        )
    return rows, multiple


def _slot_payload(cells, label_col, slot):
    value_col = label_col + 1

    def get_label(row):
        return cells.get((row, label_col))

    def get_value(row):
        return cells.get((row, value_col))

    business = _clean_text(get_value(4))
    if not business:
        return None

    current_plan = _clean_text(get_label(2))
    contact_name = _clean_text(get_label(3)) or business
    advisor = _clean_text(get_value(5))
    phone = _clean_text(get_value(6))
    other_phone = _clean_text(get_value(7))
    email = _clean_text(get_value(9)).lower()
    address = _clean_text(get_value(10))
    website = _clean_url(get_value(11))
    google_raw = _clean_text(get_value(12))

    plan_start, start_precision = parse_date_value(get_value(13))
    plan_end, end_precision = parse_date_value(get_value(14))
    domain_data = _extract_domain_dates(get_value(15))

    benefits = _benefit_text(get_label(18), get_value(18))
    courtesies = _benefit_text(get_value(19))

    platforms = []
    facebook = None
    instagram = None

    google_payload = _platform_payload("Google Business Profile / Maps", google_raw)
    if google_payload:
        google_payload["key"] = "google_business"
        google_payload["label"] = "Google Business Profile / Maps"
        platforms.append(google_payload)

    for row in range(22, 34):
        label = _clean_text(get_label(row))
        value = _clean_text(get_value(row))
        key = _platform_key(label)
        if key == "facebook":
            facebook = _clean_url(value)
            continue
        if key == "instagram":
            instagram = _clean_url(value)
            continue
        payload = _platform_payload(label, value)
        if payload:
            platforms.append(payload)

    operational = {
        "attention_days": _clean_text(get_value(35)),
        "business_hours": _clean_text(get_value(36)),
        "experience_text": _clean_text(get_value(37)),
        "coverage_text": _coverage_text(get_value(38)),
        "payment_methods": _scrub_sensitive_text(get_value(39)),
        "estimate_policy": _scrub_sensitive_text(get_value(40)),
        "operational_email": _clean_text(get_value(41)).lower(),
        # Fila 42 omitida intencionalmente: CONTRASEÑA DEL CORREO
        "corporate_email": _clean_text(get_value(43)).lower(),
        "services_to_promote": _scrub_sensitive_text(get_value(44)),
        "logo_status": _scrub_sensitive_text(get_value(45)),
        "brand_colors": _scrub_sensitive_text(get_value(46)),
        "domain_activated_on": domain_data.get("domain_activated_on"),
        "hosting_activated_on": domain_data.get("hosting_activated_on"),
        "domain_notes": _scrub_sensitive_text(get_value(15)),
    }

    investments, multiple_investments = _investment_rows(
        cells,
        label_col,
        value_col,
    )

    warnings = []
    if start_precision == "approximate":
        warnings.append("La fecha de activación del plan es aproximada (mes/año).")
    if multiple_investments:
        warnings.append(
            "Se detectaron inversiones adicionales; no se importarán ventas/pagos automáticamente."
        )
    if _clean_text(get_value(8)):
        warnings.append("Se omitió la fila CLAVE.")
    if _clean_text(get_value(42)):
        warnings.append("Se omitió la CONTRASEÑA DEL CORREO.")

    return {
        "slot": slot,
        "business_name": business,
        "contact_name": contact_name,
        "advisor": advisor,
        "phone": phone,
        "other_phones": other_phone,
        "email": email,
        "address": address,
        "website": website,
        "facebook": facebook,
        "instagram": instagram,
        "current_plan_label": current_plan,
        "plan_start": plan_start.isoformat() if plan_start else None,
        "plan_start_precision": start_precision,
        "plan_end": plan_end.isoformat() if plan_end else None,
        "plan_end_precision": end_precision,
        "benefits": benefits,
        "courtesies": courtesies,
        "operational": operational,
        "platforms": platforms,
        "investments": investments,
        "multiple_investments": multiple_investments,
        "secret_fields_skipped": int(bool(_clean_text(get_value(8))))
        + int(bool(_clean_text(get_value(42)))),
        "warnings": warnings,
    }


def parse_operational_workbook(file_bytes, file_name="workbook.xlsx"):
    if not isinstance(file_bytes, (bytes, bytearray)):
        raise WorkbookImportError("El archivo no pudo ser leído.")
    if len(file_bytes) > MAX_WORKBOOK_BYTES:
        raise WorkbookImportError(
            "El archivo excede el tamaño máximo permitido para esta importación."
        )
    if not zipfile.is_zipfile(BytesIO(file_bytes)):
        raise WorkbookImportError("El archivo no es un XLSX válido.")

    with zipfile.ZipFile(BytesIO(file_bytes)) as zf:
        sheets = _sheet_targets(zf)
        if "CLIENTES" not in sheets:
            raise WorkbookImportError("No se encontró la hoja CLIENTES.")
        shared = _shared_strings(zf)
        cells = _read_sheet_cells(zf, sheets["CLIENTES"], shared)

    label_columns = []
    for (row, col), value in cells.items():
        if row == 1 and re.fullmatch(r"#\d+", _clean_text(value)):
            label_columns.append((col, _clean_text(value)))

    label_columns.sort()
    if not label_columns:
        raise WorkbookImportError(
            "No se detectaron fichas de clientes en la hoja CLIENTES."
        )

    clients = []
    for col, slot in label_columns:
        payload = _slot_payload(cells, col, slot)
        if payload:
            clients.append(payload)

    return {
        "file_name": file_name,
        "file_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "source_sheet": "CLIENTES",
        "excluded_sheets": [
            name for name in ("TAREAS", "CONTRASEÑAS") if name in sheets
        ],
        "client_count": len(clients),
        "clients": clients,
        "security": {
            "password_sheet_imported": False,
            "secret_fields_imported": False,
            "secret_fields_skipped": sum(
                row["secret_fields_skipped"] for row in clients
            ),
        },
    }
