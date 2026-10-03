from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath


MAX_IMPORT_BYTES = 8 * 1024 * 1024
MAX_IMPORT_ROWS = 1000
MAX_IMPORT_COLS = 60

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


class TabularImportError(ValueError):
    pass


def normalize_text(value):
    text = str(value or "").strip().lower()
    text = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )
    return " ".join(text.split())


def normalize_key(value):
    text = normalize_text(value)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def phone_key(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def business_key(value):
    return normalize_text(value)


def _decode_csv(file_bytes):
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise TabularImportError("No se pudo leer el archivo CSV.")


def _parse_csv(file_bytes):
    text = _decode_csv(file_bytes)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect=dialect)
    raw_rows = list(reader)
    return _matrix_to_rows(raw_rows)


def _safe_zip_part(zf, name, max_bytes=25 * 1024 * 1024):
    try:
        info = zf.getinfo(name)
    except KeyError as exc:
        raise TabularImportError(f"El XLSX no contiene {name}.") from exc
    if info.file_size > max_bytes:
        raise TabularImportError("El XLSX contiene una sección demasiado grande.")
    return zf.read(name)


def _shared_strings(zf):
    try:
        raw = _safe_zip_part(zf, "xl/sharedStrings.xml")
    except TabularImportError:
        return []

    root = ET.fromstring(raw)
    out = []
    for node in root.findall(f"{{{_NS_MAIN}}}si"):
        parts = []
        for text_node in node.iter(f"{{{_NS_MAIN}}}t"):
            parts.append(text_node.text or "")
        out.append("".join(parts))
    return out


def _first_sheet_target(zf):
    workbook = ET.fromstring(_safe_zip_part(zf, "xl/workbook.xml"))
    rels = ET.fromstring(_safe_zip_part(zf, "xl/_rels/workbook.xml.rels"))

    rel_map = {
        rel.attrib.get("Id"): rel.attrib.get("Target", "")
        for rel in rels.findall(f"{{{_NS_PKG_REL}}}Relationship")
    }

    sheets = workbook.find(f"{{{_NS_MAIN}}}sheets")
    if sheets is None:
        raise TabularImportError("El XLSX no contiene hojas.")

    first = next(iter(sheets.findall(f"{{{_NS_MAIN}}}sheet")), None)
    if first is None:
        raise TabularImportError("El XLSX no contiene hojas.")

    rid = first.attrib.get(f"{{{_NS_REL}}}id")
    target = rel_map.get(rid, "").replace("\\", "/")
    if not target:
        raise TabularImportError("No se pudo localizar la primera hoja.")

    if target.startswith("/"):
        target = target.lstrip("/")
    elif not target.startswith("xl/"):
        target = str(PurePosixPath("xl") / target)

    return target


def _column_index(cell_ref):
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        return None
    idx = 0
    for ch in match.group(1):
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _cell_text(cell, shared):
    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        parts = [node.text or "" for node in cell.iter(f"{{{_NS_MAIN}}}t")]
        return "".join(parts).strip()

    value_node = cell.find(f"{{{_NS_MAIN}}}v")
    if value_node is None:
        return ""

    raw = value_node.text or ""
    if cell_type == "s":
        try:
            return str(shared[int(raw)]).strip()
        except (ValueError, IndexError):
            return ""

    if cell_type == "b":
        return "Sí" if raw == "1" else "No"

    return raw.strip()


def _parse_xlsx(file_bytes):
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as exc:
        raise TabularImportError("El archivo .xlsx no es válido.") from exc

    with zf:
        shared = _shared_strings(zf)
        sheet_target = _first_sheet_target(zf)
        root = ET.fromstring(_safe_zip_part(zf, sheet_target))

        sheet_data = root.find(f"{{{_NS_MAIN}}}sheetData")
        if sheet_data is None:
            raise TabularImportError("La primera hoja no contiene datos.")

        matrix = []
        for row_node in sheet_data.findall(f"{{{_NS_MAIN}}}row"):
            if len(matrix) > MAX_IMPORT_ROWS:
                raise TabularImportError(
                    f"El archivo supera el máximo de {MAX_IMPORT_ROWS} filas."
                )

            values = {}
            max_index = -1
            for cell in row_node.findall(f"{{{_NS_MAIN}}}c"):
                idx = _column_index(cell.attrib.get("r"))
                if idx is None:
                    continue
                if idx >= MAX_IMPORT_COLS:
                    raise TabularImportError(
                        f"El archivo supera el máximo de {MAX_IMPORT_COLS} columnas."
                    )
                values[idx] = _cell_text(cell, shared)
                max_index = max(max_index, idx)

            if max_index >= 0:
                matrix.append([values.get(i, "") for i in range(max_index + 1)])

        return _matrix_to_rows(matrix)


def _matrix_to_rows(matrix):
    non_empty = [
        [str(value or "").strip() for value in row]
        for row in matrix
        if any(str(value or "").strip() for value in row)
    ]
    if not non_empty:
        raise TabularImportError("El archivo no contiene filas con datos.")

    headers = [normalize_key(value) for value in non_empty[0]]
    if not any(headers):
        raise TabularImportError("El archivo no contiene encabezados válidos.")

    seen = set()
    safe_headers = []
    for idx, header in enumerate(headers):
        header = header or f"columna_{idx + 1}"
        base = header
        suffix = 2
        while header in seen:
            header = f"{base}_{suffix}"
            suffix += 1
        seen.add(header)
        safe_headers.append(header)

    rows = []
    for line_number, raw in enumerate(non_empty[1:], start=2):
        if len(rows) >= MAX_IMPORT_ROWS:
            raise TabularImportError(
                f"El archivo supera el máximo de {MAX_IMPORT_ROWS} registros."
            )
        row = {
            safe_headers[idx]: (raw[idx].strip() if idx < len(raw) else "")
            for idx in range(len(safe_headers))
        }
        if any(row.values()):
            row["_line"] = line_number
            rows.append(row)

    return {"headers": safe_headers, "rows": rows}


def parse_tabular_file(file_bytes, file_name):
    if len(file_bytes) > MAX_IMPORT_BYTES:
        raise TabularImportError(
            f"El archivo supera el máximo de {MAX_IMPORT_BYTES // (1024 * 1024)} MB."
        )

    lower = (file_name or "").lower()
    if lower.endswith(".csv"):
        parsed = _parse_csv(file_bytes)
        parsed["format"] = "csv"
    elif lower.endswith(".xlsx"):
        parsed = _parse_xlsx(file_bytes)
        parsed["format"] = "xlsx"
    else:
        raise TabularImportError("Solo se permiten archivos .csv o .xlsx.")

    parsed["file_sha256"] = hashlib.sha256(file_bytes).hexdigest()
    parsed["file_size"] = len(file_bytes)
    return parsed


ALIASES = {
    "business_name": (
        "business_name", "negocio", "empresa", "compania", "compañia",
        "nombre_empresa", "nombre_de_la_empresa",
    ),
    "contact_name": (
        "contact_name", "contacto", "nombre_contacto", "nombre_del_contacto",
    ),
    "phone": (
        "phone", "telefono", "teléfono", "telefono_principal",
        "contacto_ppal", "contacto_principal",
    ),
    "email": ("email", "correo", "correo_electronico", "correo_electrónico"),
    "source": ("source", "fuente", "origen"),
    "pipeline_stage": ("pipeline_stage", "etapa", "estado_pipeline"),
    "record_type": ("record_type", "tipo_registro", "tipo"),
    "priority": ("priority", "prioridad"),
    "advisor": ("advisor", "asesor", "responsable", "owner"),
    "notes": ("notes", "notas", "observaciones"),
    "industry": ("industry", "industria", "rubro"),
    "website": ("website", "sitio_web", "web"),
    "city": ("city", "ciudad"),
    "state": ("state", "estado", "provincia"),
}


def pick(row, field):
    for alias in ALIASES[field]:
        key = normalize_key(alias)
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def canonical_contact_row(row):
    business = pick(row, "business_name")
    contact = pick(row, "contact_name") or business
    email = pick(row, "email").lower()
    phone = pick(row, "phone")

    return {
        "line": int(row.get("_line") or 0),
        "business_name": business,
        "contact_name": contact,
        "phone": phone,
        "email": email,
        "source": pick(row, "source"),
        "pipeline_stage": normalize_key(pick(row, "pipeline_stage")),
        "record_type": normalize_key(pick(row, "record_type")),
        "priority": normalize_key(pick(row, "priority")),
        "advisor": pick(row, "advisor"),
        "notes": pick(row, "notes"),
        "industry": pick(row, "industry"),
        "website": pick(row, "website"),
        "city": pick(row, "city"),
        "state": pick(row, "state"),
    }
