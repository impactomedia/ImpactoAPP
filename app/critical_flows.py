from __future__ import annotations

from datetime import date, datetime

from flask import flash, redirect, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Client, PrintOrder, Project, Task


def _required_project_blockers(project_id):
    return (
        Task.query
        .filter(
            Task.project_id == project_id,
            Task.checklist.isnot(None),
            Task.checklist != "",
            Task.status.notin_(["completada", "cancelada"]),
        )
        .order_by(Task.id)
        .all()
    )


def init_critical_flow_guards(app):
    @app.before_request
    def enforce_critical_flow_rules():
        if (
            request.method != "POST"
            or not current_user.is_authenticated
        ):
            return None

        endpoint = request.endpoint or ""

        if endpoint == "crm.transfer":
            client_id = (request.view_args or {}).get("client_id")
            client = (
                db.session.get(Client, client_id)
                if client_id
                else None
            )
            new_owner_id = request.form.get("owner_id", type=int)
            reason = (request.form.get("reason") or "").strip()
            if (
                client
                and new_owner_id
                and new_owner_id != client.owner_id
                and not reason
            ):
                flash(
                    "La reasignación de responsable comercial requiere un motivo.",
                    "danger",
                )
                return redirect(
                    url_for("crm.detail", client_id=client.id)
                )

        if endpoint == "printing.create_shipment":
            order_id = (request.view_args or {}).get("order_id")
            order = db.session.get(PrintOrder, order_id) if order_id else None

            shipped_raw = (request.form.get("shipped_at") or "").strip()
            estimated_raw = (
                request.form.get("estimated_delivery") or ""
            ).strip()

            if not estimated_raw:
                flash(
                    "La fecha aproximada de recepción es obligatoria al registrar un envío.",
                    "danger",
                )
                if order:
                    return redirect(
                        url_for("printing.detail", order_id=order.id)
                    )
                return redirect(url_for("printing.index"))

            try:
                estimated = date.fromisoformat(estimated_raw)
            except ValueError:
                flash(
                    "La fecha aproximada de recepción no es válida.",
                    "danger",
                )
                if order:
                    return redirect(
                        url_for("printing.detail", order_id=order.id)
                    )
                return redirect(url_for("printing.index"))

            shipped = datetime.utcnow()
            if shipped_raw:
                try:
                    shipped = datetime.fromisoformat(shipped_raw)
                except ValueError:
                    flash(
                        "La fecha real de envío no es válida.",
                        "danger",
                    )
                    if order:
                        return redirect(
                            url_for(
                                "printing.detail",
                                order_id=order.id,
                            )
                        )
                    return redirect(url_for("printing.index"))

            if estimated < shipped.date():
                flash(
                    "La fecha aproximada de recepción no puede ser anterior a la fecha real de envío.",
                    "danger",
                )
                if order:
                    return redirect(
                        url_for("printing.detail", order_id=order.id)
                    )
                return redirect(url_for("printing.index"))

        if (
            endpoint == "operations.update_project"
            and (request.form.get("status") or "").strip()
            == "completado"
        ):
            project_id = (request.view_args or {}).get("project_id")
            project = (
                db.session.get(Project, project_id)
                if project_id
                else None
            )
            if project:
                blockers = _required_project_blockers(project.id)
                if blockers:
                    names = ", ".join(
                        task.title for task in blockers[:3]
                    )
                    extra = (
                        f" y {len(blockers) - 3} más"
                        if len(blockers) > 3
                        else ""
                    )
                    flash(
                        (
                            "No puedes completar el proyecto mientras existan "
                            f"requisitos obligatorios pendientes: {names}{extra}."
                        ),
                        "warning",
                    )
                    return redirect(
                        url_for(
                            "operations.project_detail",
                            project_id=project.id,
                        )
                    )

        return None
