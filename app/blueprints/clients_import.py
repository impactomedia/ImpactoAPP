from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.decorators import permission_required, roles_required
from app.extensions import db
from app.helpers import audit
from app.client_excel_parser import (
    WorkbookImportError,
    parse_operational_workbook,
)
from app.client_excel_importer import (
    analyze_batch_payload,
    execute_batch,
)
from app.client_v2_models import ClientImportBatch


bp = Blueprint(
    "clients_import",
    __name__,
    url_prefix="/clients/import-workbook",
)


def _recent_batches():
    return (
        ClientImportBatch.query
        .order_by(ClientImportBatch.created_at.desc())
        .limit(10)
        .all()
    )


@bp.route("/", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
@permission_required("clients.create")
def index():
    if request.method == "POST":
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            flash("Selecciona el archivo XLSX operativo.", "danger")
            return redirect(url_for("clients_import.index"))

        if not uploaded.filename.lower().endswith(".xlsx"):
            flash("Esta importación solo acepta archivos .xlsx.", "danger")
            return redirect(url_for("clients_import.index"))

        file_bytes = uploaded.read()
        file_name = secure_filename(
            Path(uploaded.filename).name
        ) or "fichas_operativas.xlsx"

        try:
            payload = parse_operational_workbook(
                file_bytes,
                file_name=file_name,
            )
        except WorkbookImportError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("clients_import.index"))

        batch = ClientImportBatch(
            file_name=file_name,
            file_sha256=payload["file_sha256"],
            file_size=len(file_bytes),
            status="previewed",
            client_count=payload["client_count"],
            payload_json=payload,
            created_by_id=current_user.id,
        )
        db.session.add(batch)
        db.session.flush()

        audit(
            "previsualizar_importacion_excel_operativo",
            "ClientImportBatch",
            batch.id,
            after={
                "file_name": file_name,
                "client_count": payload["client_count"],
                "excluded_sheets": payload["excluded_sheets"],
                "secret_fields_skipped": payload["security"][
                    "secret_fields_skipped"
                ],
                "secret_fields_imported": False,
            },
        )
        db.session.commit()

        flash(
            (
                f"Vista previa lista: {payload['client_count']} ficha(s) "
                "detectada(s). Ninguna contraseña fue importada."
            ),
            "success",
        )
        return redirect(
            url_for(
                "clients_import.preview",
                batch_id=batch.id,
            )
        )

    return render_template(
        "clients/import_workbook.html",
        batch=None,
        analysis=[],
        recent_batches=_recent_batches(),
    )


@bp.route("/<int:batch_id>")
@login_required
@roles_required("superadmin", "admin", "manager")
@permission_required("clients.create")
def preview(batch_id):
    batch = db.get_or_404(ClientImportBatch, batch_id)
    analysis = analyze_batch_payload(batch.payload_json)

    return render_template(
        "clients/import_workbook.html",
        batch=batch,
        analysis=analysis,
        recent_batches=_recent_batches(),
    )


@bp.route("/<int:batch_id>/execute", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager")
@permission_required("clients.create")
def execute(batch_id):
    batch = db.get_or_404(ClientImportBatch, batch_id)

    if batch.status == "completed":
        flash("Este lote ya fue importado.", "info")
        return redirect(
            url_for(
                "clients_import.preview",
                batch_id=batch.id,
            )
        )

    if not request.form.get("confirm_import"):
        flash(
            "Debes confirmar que revisaste la vista previa.",
            "danger",
        )
        return redirect(
            url_for(
                "clients_import.preview",
                batch_id=batch.id,
            )
        )

    try:
        result = execute_batch(
            batch,
            create_missing=bool(
                request.form.get("create_missing")
            ),
            overwrite_existing=bool(
                request.form.get("overwrite_existing")
            ),
            replace_owner=bool(
                request.form.get("replace_owner")
            ),
            import_contracts=bool(
                request.form.get("import_contracts")
            ),
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falló la importación controlada del lote %s",
            batch_id,
        )
        failed_batch = db.session.get(
            ClientImportBatch,
            batch_id,
        )
        if failed_batch:
            failed_batch.status = "failed"
            failed_batch.result_json = {
                "error": (
                    "La importación no pudo completarse. "
                    "No se aplicaron cambios parciales."
                )
            }
            db.session.commit()

        flash(
            (
                "La importación no pudo completarse y fue revertida. "
                "Revisa los logs antes de volver a intentar."
            ),
            "danger",
        )
        return redirect(
            url_for(
                "clients_import.preview",
                batch_id=batch_id,
            )
        )

    flash(
        (
            "Importación completada: "
            f"{result['created_clients']} cliente(s) creado(s), "
            f"{result['updated_clients']} actualizado(s) y "
            f"{result['skipped_clients']} omitido(s)."
        ),
        "success",
    )
    return redirect(
        url_for(
            "clients_import.preview",
            batch_id=batch.id,
        )
    )
