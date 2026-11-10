"""Auditoría no destructiva de plantillas, navegación, textos y pantallas.

Uso:
    python -m scripts.quality_audit
    python -m scripts.quality_audit --strict
El modo informe no bloquea CI: clasifica problemas para su revisión.
"""
from __future__ import annotations

import argparse
import ast
import re
from collections import Counter
from pathlib import Path

from app import create_app
from app.extensions import db
from scripts.seed import seed_all

BASE = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = BASE / "app" / "templates"
PY_ROOT = BASE / "app"
LITERAL_URL_FOR = re.compile(r"""url_for\(\s*['"]([a-zA-Z_][a-zA-Z_0-9.]*)['"]""")
RENDER_TEMPLATE = re.compile(r"""render_template\(\s*['"]([^'"]+\.html)['"]""")
TYPO_PATTERNS = {
    "inverción": "inversión",
    "inverciones": "inversiones",
    "comición": "comisión",
    "comiciones": "comisiones",
    "renobación": "renovación",
    "renobar": "renovar",
    "proximo": "próximo",
    "proxima": "próxima",
    "configuracion": "configuración",
    "informacion": "información",
    "contraseñaas": "contraseñas",
    "estimacion": "estimación",
    "metodo de pago": "método de pago",
}
SKIP_GET = (
    "logout", "export", "download", "backup", "restore", "purge",
    "delete", "reset", "attachment", "statement", "healthz",
    "oauth", "token", "report_pdf", "csv", "pdf", "sitemap",
)


class Report:
    def __init__(self):
        self.entries = []

    def add(self, severity, category, where, description):
        self.entries.append((severity, category, str(where), description))

    def display(self):
        counts = Counter((level, category) for level, category, _, _ in self.entries)
        print("\n=== AUDITORÍA DE IMPACTO APP ===")
        for (level, category), count in sorted(counts.items()):
            print(f"{level:7s} {category:22s}: {count}")
        if not self.entries:
            print("Sin hallazgos en las comprobaciones automatizadas.")
        for severity, category, where, description in self.entries[:150]:
            print(f"[{severity}] [{category}] {where}: {description}")
        if len(self.entries) > 150:
            print(f"... {len(self.entries) - 150} entradas adicionales omitidas.")
        print("=== FIN DE AUDITORÍA ===\n")


def audit_templates(app, report):
    files = sorted(TEMPLATE_ROOT.rglob("*.html"))
    endpoint_names = set(app.view_functions)
    existing_names = {str(f.relative_to(TEMPLATE_ROOT)) for f in files}
    stats = Counter()

    for path in files:
        relative = str(path.relative_to(TEMPLATE_ROOT))
        text = path.read_text(encoding="utf-8")
        try:
            app.jinja_env.parse(text)
        except Exception as exc:
            report.add("ERROR", "jinja_syntax", relative, repr(exc))
        stats["templates"] += 1

        for match in LITERAL_URL_FOR.finditer(text):
            endpoint = match.group(1)
            if endpoint not in endpoint_names:
                line = text.count("\n", 0, match.start()) + 1
                report.add("ERROR", "broken_url_for", f"{relative}:{line}", endpoint)

        for typo, correction in TYPO_PATTERNS.items():
            # Candidate warnings: they may occur in JS identifiers rather than visible copy.
            for match in re.finditer(r"\b" + re.escape(typo) + r"\b", text, re.IGNORECASE):
                line = text.count("\n", 0, match.start()) + 1
                report.add("WARN", "copy_candidate", f"{relative}:{line}", f"{typo} -> {correction}")

    for path in PY_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        try:
            ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            report.add("ERROR", "python_syntax", str(path.relative_to(BASE)), str(exc))
        for match in RENDER_TEMPLATE.finditer(text):
            template = match.group(1)
            if template not in existing_names:
                line = text.count("\n", 0, match.start()) + 1
                report.add(
                    "ERROR", "missing_template",
                    f"{path.relative_to(BASE)}:{line}", template,
                )

    print(f"Plantillas compiladas: {stats['templates']}")
    print(f"Endpoints registrados: {len(endpoint_names)}")


def audit_smoke_routes(app, report):
    from flask import url_for

    with app.app_context():
        db.create_all()
        seed_all()
        browser = app.test_client()
        response = browser.post(
            "/auth/login",
            data={"email": "admin@impactomedia.local", "password": "ChangeMe123!"},
        )
        if response.status_code not in (302, 303):
            report.add("ERROR", "login_fixture", "/auth/login", f"HTTP {response.status_code}")
            return

        checks = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint == "static" or "GET" not in rule.methods or rule.arguments:
                continue
            if any(word in (rule.rule + " " + rule.endpoint).lower() for word in SKIP_GET):
                continue
            checks.append((rule.rule, rule.endpoint))
        checks = sorted(set(checks))
        stats = Counter()
        for path, endpoint in checks:
            try:
                response = browser.get(path, follow_redirects=False)
                stats[response.status_code] += 1
                if response.status_code >= 500 or response.status_code == 404:
                    report.add("ERROR", "get_route", f"{endpoint} {path}", f"HTTP {response.status_code}")
                elif response.status_code in (401, 403):
                    report.add("WARN", "get_permission", f"{endpoint} {path}", f"HTTP {response.status_code}")
            except Exception as exc:
                report.add("ERROR", "get_exception", f"{endpoint} {path}", repr(exc))
        print(f"Rutas GET verificadas: {len(checks)}, respuestas: {dict(stats)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="Salir con error si hay hallazgos graves")
    args = parser.parse_args()
    class AuditConfig:
        TESTING = True
        SECRET_KEY = "quality-audit-test-secret"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        WTF_CSRF_ENABLED = False
        UPLOAD_FOLDER = "/tmp/impacto-ui-audit-uploads"
        MAX_CONTENT_LENGTH = 10 * 1024 * 1024
        COMPANY_NAME = "Impacto Media Agency"
        COMPANY_TIMEZONE = "America/Managua"
        SMTP_HOST = None
        SMTP_PORT = 587
        SMTP_USER = None
        SMTP_PASSWORD = None
        SMTP_FROM = "test@example.com"
        SMTP_USE_TLS = False

    app = create_app(AuditConfig)
    report = Report()
    audit_templates(app, report)
    audit_smoke_routes(app, report)
    report.display()
    if args.strict and any(entry[0] == "ERROR" for entry in report.entries):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
