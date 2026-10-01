from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import roles_required
from app.extensions import db
from app.helpers import audit, next_code, save_upload
from app.models import (
    AdvisorProject,
    AttendanceMark,
    Collaborator,
    CollaboratorDocument,
    Evaluation,
    LeaveRequest,
    Role,
    ScheduleAssignment,
    Training,
    User,
    VacationMovement,
    WorkSchedule,
)

bp = Blueprint("hr", __name__, url_prefix="/hr")

MARK_TYPES = ["entrada", "break_inicio", "break_fin", "almuerzo_inicio", "almuerzo_fin", "salida"]
LEAVE_TYPES = {"vacaciones", "permiso", "ausencia"}


def _role_name():
    return current_user.role.name if current_user.role else ""


def _supervisor_team_ids(include_self=True):
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    ids = [row.id for row in collaborator.subordinates]
    if include_self:
        ids.insert(0, collaborator.id)
    return ids


def _visible_collaborator_query():
    query = Collaborator.query
    if _role_name() == "supervisor" and current_user.collaborator:
        query = query.filter(Collaborator.id.in_(_supervisor_team_ids()))
    return query


def _allowed_mark_types(last_mark):
    return {
        None: ["entrada"],
        "entrada": ["break_inicio", "almuerzo_inicio", "salida"],
        "break_inicio": ["break_fin"],
        "break_fin": ["break_inicio", "almuerzo_inicio", "salida"],
        "almuerzo_inicio": ["almuerzo_fin"],
        "almuerzo_fin": ["break_inicio", "salida"],
        "salida": [],
    }.get(last_mark, [])


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@bp.route("/")
@login_required
def dashboard():
    today = date.today()
    collaborators = _visible_collaborator_query().order_by(Collaborator.status, Collaborator.id).all()
    collaborator_ids = [row.id for row in collaborators]

    marks_query = AttendanceMark.query.filter(db.func.date(AttendanceMark.marked_at) == today)
    leaves_query = LeaveRequest.query.filter(
        LeaveRequest.start_date <= today,
        LeaveRequest.end_date >= today,
        LeaveRequest.status == "aprobada",
    )
    if _role_name() == "supervisor":
        marks_query = marks_query.filter(AttendanceMark.collaborator_id.in_(collaborator_ids))
        leaves_query = leaves_query.filter(LeaveRequest.collaborator_id.in_(collaborator_ids))

    today_marks = marks_query.order_by(AttendanceMark.marked_at.desc()).all()
    leaves = leaves_query.all()
    return render_template("hr/dashboard.html", collaborators=collaborators, today_marks=today_marks, leaves=leaves)


@bp.route("/collaborators", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def collaborators():
    roles = Role.query.filter_by(active=True).order_by(Role.label).all()
    supervisors = Collaborator.query.filter_by(status="activo").order_by(Collaborator.id).all()
    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()
    selected_project_id = request.args.get("advisor_project_id", type=int)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        role = db.session.get(Role, request.form.get("role_id", type=int))

        if not name or not email:
            flash("Nombre y correo son obligatorios.", "danger")
            return redirect(url_for("hr.collaborators"))
        if not role or not role.active:
            flash("Selecciona un rol válido y activo.", "danger")
            return redirect(url_for("hr.collaborators"))
        if len(password) < 8:
            flash("La contraseña inicial debe tener al menos 8 caracteres.", "danger")
            return redirect(url_for("hr.collaborators"))
        if User.query.filter_by(email=email).first():
            flash("Ya existe un usuario con ese correo.", "danger")
            return redirect(url_for("hr.collaborators"))

        advisor_project_id = request.form.get("advisor_project_id", type=int)
        if role.name == "advisor" and not advisor_project_id:
            flash("Los asesores deben pertenecer al proyecto IMPACTO o NOVAX.", "warning")
            return redirect(url_for("hr.collaborators"))

        supervisor_id = request.form.get("supervisor_id", type=int)
        if supervisor_id and not db.session.get(Collaborator, supervisor_id):
            flash("El supervisor seleccionado no existe.", "danger")
            return redirect(url_for("hr.collaborators"))

        base_salary = request.form.get("base_salary") or 0
        try:
            base_salary = Decimal(str(base_salary))
        except InvalidOperation:
            flash("El salario base no es válido.", "danger")
            return redirect(url_for("hr.collaborators"))
        if base_salary < 0:
            flash("El salario base no puede ser negativo.", "danger")
            return redirect(url_for("hr.collaborators"))

        user = User(name=name, email=email, role=role, active=True)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        collaborator = Collaborator(
            user_id=user.id,
            code=next_code("COL", Collaborator),
            phone=request.form.get("phone"),
            corporate_email=email,
            job_title=(request.form.get("job_title") or "Colaborador").strip(),
            department=(request.form.get("department") or "").strip() or None,
            advisor_project_id=advisor_project_id,
            supervisor_id=supervisor_id,
            join_date=_parse_date(request.form.get("join_date")) or date.today(),
            contract_type=request.form.get("contract_type"),
            status="activo",
            work_mode=request.form.get("work_mode", "presencial"),
            base_salary=base_salary,
            vacation_rate=request.form.get("vacation_rate") or 0,
            vacation_balance=request.form.get("vacation_balance") or 0,
        )
        db.session.add(collaborator)
        audit("crear_colaborador", "Collaborator", after={"email": email, "job_title": collaborator.job_title})
        db.session.commit()
        flash("Colaborador creado.", "success")
        return redirect(url_for("hr.collaborators"))

    query = Collaborator.query
    if selected_project_id:
        query = query.filter(Collaborator.advisor_project_id == selected_project_id)
    rows = query.order_by(Collaborator.id.desc()).all()
    return render_template(
        "hr/collaborators.html",
        collaborators=rows,
        roles=roles,
        supervisors=supervisors,
        advisor_projects=advisor_projects,
        selected_project_id=selected_project_id,
    )


@bp.route("/collaborators/<int:collaborator_id>", methods=["GET", "POST"])
@login_required
def collaborator_detail(collaborator_id):
    collaborator = db.get_or_404(Collaborator, collaborator_id)
    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()

    if request.method == "POST":
        if not current_user.has_permission("hr.edit"):
            abort(403)

        status = request.form.get("status", collaborator.status)
        if status not in {"activo", "inactivo", "suspendido"}:
            status = collaborator.status

        collaborator.job_title = (request.form.get("job_title") or collaborator.job_title).strip()
        collaborator.department = (request.form.get("department") or "").strip() or None
        collaborator.advisor_project_id = request.form.get("advisor_project_id", type=int)
        collaborator.status = status

        if current_user.has_permission("hr.sensitive"):
            base_salary_raw = request.form.get("base_salary")
            vacation_balance_raw = request.form.get("vacation_balance")
            vacation_rate_raw = request.form.get("vacation_rate")
            try:
                if base_salary_raw not in (None, ""):
                    base_salary = Decimal(str(base_salary_raw))
                    if base_salary < 0:
                        raise InvalidOperation
                    collaborator.base_salary = base_salary
                if vacation_balance_raw not in (None, ""):
                    collaborator.vacation_balance = Decimal(str(vacation_balance_raw))
                if vacation_rate_raw not in (None, ""):
                    collaborator.vacation_rate = Decimal(str(vacation_rate_raw))
            except InvalidOperation:
                flash("Revisa los valores numéricos del expediente.", "danger")
                return redirect(url_for("hr.collaborator_detail", collaborator_id=collaborator.id))

        audit("editar_colaborador", "Collaborator", collaborator.id)
        db.session.commit()
        flash("Expediente actualizado.", "success")

    return render_template("hr/collaborator_detail.html", collaborator=collaborator, advisor_projects=advisor_projects)


@bp.route("/schedules", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def schedules():
    if request.method == "POST":
        name = (request.form.get("name") or "Horario").strip()
        try:
            start_time = time.fromisoformat(request.form.get("start_time")) if request.form.get("start_time") else None
            end_time = time.fromisoformat(request.form.get("end_time")) if request.form.get("end_time") else None
        except ValueError:
            flash("Revisa las horas del horario.", "danger")
            return redirect(url_for("hr.schedules"))

        schedule = WorkSchedule(
            name=name,
            workdays=request.form.get("workdays", "0,1,2,3,4"),
            start_time=start_time,
            end_time=end_time,
            lunch_minutes=max(0, request.form.get("lunch_minutes", type=int) or 60),
            break_minutes=max(0, request.form.get("break_minutes", type=int) or 30),
            tolerance_minutes=max(0, request.form.get("tolerance_minutes", type=int) or 10),
        )
        db.session.add(schedule)
        db.session.commit()
        flash("Horario creado.", "success")
        return redirect(url_for("hr.schedules"))

    return render_template(
        "hr/schedules.html",
        schedules=WorkSchedule.query.order_by(WorkSchedule.id.desc()).all(),
        collaborators=Collaborator.query.filter_by(status="activo").all(),
    )


@bp.route("/schedules/assign", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def assign_schedule():
    collaborator = db.session.get(Collaborator, request.form.get("collaborator_id", type=int))
    schedule = db.session.get(WorkSchedule, request.form.get("schedule_id", type=int))
    if not collaborator or not schedule:
        flash("Selecciona colaborador y horario válidos.", "danger")
        return redirect(url_for("hr.schedules"))

    starts_on = _parse_date(request.form.get("starts_on")) or date.today()
    ends_on = _parse_date(request.form.get("ends_on"))
    if ends_on and ends_on < starts_on:
        flash("La fecha final del horario no puede ser anterior al inicio.", "danger")
        return redirect(url_for("hr.schedules"))

    db.session.add(
        ScheduleAssignment(
            collaborator_id=collaborator.id,
            schedule_id=schedule.id,
            starts_on=starts_on,
            ends_on=ends_on,
        )
    )
    db.session.commit()
    flash("Horario asignado.", "success")
    return redirect(url_for("hr.schedules"))


@bp.route("/my-day")
@login_required
def my_day():
    collaborator = current_user.collaborator
    if not collaborator:
        flash("Tu usuario no tiene expediente de colaborador.", "warning")
        return redirect(url_for("dashboard.index"))

    today = date.today()
    marks = AttendanceMark.query.filter(
        AttendanceMark.collaborator_id == collaborator.id,
        db.func.date(AttendanceMark.marked_at) == today,
    ).order_by(AttendanceMark.marked_at).all()
    last = marks[-1].mark_type if marks else None
    return render_template(
        "hr/my_day.html",
        collaborator=collaborator,
        marks=marks,
        allowed=_allowed_mark_types(last),
    )


@bp.route("/my-day/mark", methods=["POST"])
@login_required
def mark():
    collaborator = current_user.collaborator
    if not collaborator:
        flash("No tienes expediente de colaborador.", "danger")
        return redirect(url_for("dashboard.index"))

    today = date.today()
    marks = AttendanceMark.query.filter(
        AttendanceMark.collaborator_id == collaborator.id,
        db.func.date(AttendanceMark.marked_at) == today,
    ).order_by(AttendanceMark.marked_at).all()
    last = marks[-1].mark_type if marks else None
    allowed = _allowed_mark_types(last)
    mark_type = request.form.get("mark_type")

    if mark_type not in MARK_TYPES or mark_type not in allowed:
        flash("La marcación solicitada no corresponde al orden actual de tu jornada.", "danger")
        return redirect(url_for("hr.my_day"))

    row = AttendanceMark(
        collaborator_id=collaborator.id,
        mark_type=mark_type,
        note=request.form.get("note"),
    )
    db.session.add(row)
    audit("marcacion", "AttendanceMark", after={"collaborator_id": collaborator.id, "type": mark_type})
    db.session.commit()
    flash("Marcación registrada.", "success")
    return redirect(url_for("hr.my_day"))


@bp.route("/attendance")
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def attendance():
    selected = request.args.get("date")
    day = _parse_date(selected) or date.today()
    query = AttendanceMark.query.filter(db.func.date(AttendanceMark.marked_at) == day)
    if _role_name() == "supervisor" and current_user.collaborator:
        query = query.filter(AttendanceMark.collaborator_id.in_(_supervisor_team_ids()))
    marks = query.order_by(AttendanceMark.marked_at).all()
    return render_template("hr/attendance.html", marks=marks, day=day)


@bp.route("/leave", methods=["GET", "POST"])
@login_required
def leave():
    collaborator = current_user.collaborator
    if request.method == "POST":
        if not collaborator:
            flash("No tienes expediente de colaborador.", "danger")
            return redirect(url_for("hr.leave"))

        start = _parse_date(request.form.get("start_date"))
        end = _parse_date(request.form.get("end_date"))
        if not start or not end:
            flash("Indica fechas válidas para la solicitud.", "danger")
            return redirect(url_for("hr.leave"))
        if end < start:
            flash("La fecha final no puede ser anterior a la inicial.", "danger")
            return redirect(url_for("hr.leave"))

        leave_type = request.form.get("leave_type", "vacaciones")
        if leave_type not in LEAVE_TYPES:
            leave_type = "ausencia"
        days = Decimal((end - start).days + 1)

        if leave_type == "vacaciones" and Decimal(str(collaborator.vacation_balance or 0)) < days:
            flash("El saldo disponible de vacaciones no cubre la solicitud.", "danger")
            return redirect(url_for("hr.leave"))

        overlapping = LeaveRequest.query.filter(
            LeaveRequest.collaborator_id == collaborator.id,
            LeaveRequest.status.in_(["pendiente", "aprobada"]),
            LeaveRequest.start_date <= end,
            LeaveRequest.end_date >= start,
        ).first()
        if overlapping:
            flash("Ya existe una solicitud pendiente o aprobada que se cruza con esas fechas.", "warning")
            return redirect(url_for("hr.leave"))

        row = LeaveRequest(
            collaborator_id=collaborator.id,
            leave_type=leave_type,
            start_date=start,
            end_date=end,
            days=days,
            reason=request.form.get("reason"),
            status="pendiente",
        )
        db.session.add(row)
        audit("solicitar_ausencia", "LeaveRequest", after={"days": str(days), "type": row.leave_type})
        db.session.commit()
        flash("Solicitud enviada.", "success")
        return redirect(url_for("hr.leave"))

    rows = (
        LeaveRequest.query.filter_by(collaborator_id=collaborator.id)
        .order_by(LeaveRequest.created_at.desc())
        .all()
        if collaborator
        else []
    )
    return render_template("hr/leave.html", rows=rows, collaborator=collaborator)


@bp.route("/leave/admin")
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def leave_admin():
    query = LeaveRequest.query
    if _role_name() == "supervisor" and current_user.collaborator:
        query = query.filter(LeaveRequest.collaborator_id.in_(_supervisor_team_ids()))
    rows = query.order_by(LeaveRequest.created_at.desc()).all()
    return render_template("hr/leave_admin.html", rows=rows)


@bp.route("/leave/<int:leave_id>/<decision>", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def leave_decision(leave_id, decision):
    row = db.get_or_404(LeaveRequest, leave_id)
    if decision not in {"aprobar", "rechazar", "cancelar"}:
        flash("Decisión inválida.", "danger")
        return redirect(url_for("hr.leave_admin"))

    if decision in {"aprobar", "rechazar"} and row.status != "pendiente":
        flash("Esta solicitud ya fue procesada y no puede decidirse nuevamente.", "warning")
        return redirect(url_for("hr.leave_admin"))
    if decision == "cancelar" and row.status not in {"pendiente", "aprobada"}:
        flash("Esta solicitud no se puede cancelar desde su estado actual.", "warning")
        return redirect(url_for("hr.leave_admin"))

    if decision == "aprobar":
        if row.leave_type == "vacaciones":
            collaborator = row.collaborator
            balance = Decimal(str(collaborator.vacation_balance or 0))
            days = Decimal(str(row.days or 0))
            if balance < days:
                flash("El colaborador ya no tiene saldo suficiente para aprobar estas vacaciones.", "danger")
                return redirect(url_for("hr.leave_admin"))
            collaborator.vacation_balance = balance - days
            db.session.add(
                VacationMovement(
                    collaborator_id=collaborator.id,
                    movement_type="uso",
                    days=-days,
                    effective_date=row.start_date,
                    leave_request_id=row.id,
                    note="Vacaciones aprobadas",
                    created_by_id=current_user.id,
                )
            )
        row.status = "aprobada"
        row.approver_id = current_user.id
        row.decided_at = datetime.utcnow()

    elif decision == "rechazar":
        row.status = "rechazada"
        row.approver_id = current_user.id
        row.decided_at = datetime.utcnow()

    else:
        if row.status == "aprobada" and row.leave_type == "vacaciones":
            collaborator = row.collaborator
            days = Decimal(str(row.days or 0))
            collaborator.vacation_balance = Decimal(str(collaborator.vacation_balance or 0)) + days
            db.session.add(
                VacationMovement(
                    collaborator_id=collaborator.id,
                    movement_type="reversion",
                    days=days,
                    effective_date=date.today(),
                    leave_request_id=row.id,
                    note="Cancelación de vacaciones aprobadas",
                    created_by_id=current_user.id,
                )
            )
        row.status = "cancelada"
        row.approver_id = current_user.id
        row.decided_at = datetime.utcnow()

    audit("decision_ausencia", "LeaveRequest", row.id, after={"status": row.status})
    db.session.commit()
    flash("Solicitud actualizada.", "success")
    return redirect(url_for("hr.leave_admin"))


@bp.route("/evaluations", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def evaluations():
    collaborators_query = Collaborator.query.filter_by(status="activo")
    rows_query = Evaluation.query

    if _role_name() == "supervisor" and current_user.collaborator:
        subordinate_ids = _supervisor_team_ids(include_self=False)
        collaborators_query = collaborators_query.filter(Collaborator.id.in_(subordinate_ids))
        rows_query = rows_query.filter(Evaluation.collaborator_id.in_(subordinate_ids))

    collaborators = collaborators_query.order_by(Collaborator.id).all()
    allowed_ids = {row.id for row in collaborators}

    if request.method == "POST":
        collaborator_id = request.form.get("collaborator_id", type=int)
        if not collaborator_id or collaborator_id not in allowed_ids:
            abort(403)

        try:
            score = Decimal(str(request.form.get("score") or 0))
        except InvalidOperation:
            score = Decimal("-1")
        if score < 0 or score > 100:
            flash("La calificación debe estar entre 0 y 100.", "danger")
            return redirect(url_for("hr.evaluations"))

        row = Evaluation(
            collaborator_id=collaborator_id,
            evaluator_id=current_user.id,
            evaluation_date=_parse_date(request.form.get("evaluation_date")) or date.today(),
            score=score,
            period=request.form.get("period"),
            notes=request.form.get("notes"),
        )
        db.session.add(row)
        audit("evaluacion", "Evaluation", after={"collaborator_id": row.collaborator_id, "score": str(row.score)})
        db.session.commit()
        flash("Evaluación registrada.", "success")
        return redirect(url_for("hr.evaluations"))

    return render_template(
        "hr/evaluations.html",
        rows=rows_query.order_by(Evaluation.evaluation_date.desc()).all(),
        collaborators=collaborators,
    )


@bp.route("/trainings", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def trainings():
    collaborators = Collaborator.query.filter_by(status="activo").all()
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        training_date = _parse_date(request.form.get("training_date"))
        if not title or not training_date:
            flash("Título y fecha son obligatorios para la capacitación.", "danger")
            return redirect(url_for("hr.trainings"))

        status = request.form.get("status", "programada")
        if status not in {"programada", "realizada", "cancelada"}:
            status = "programada"

        row = Training(
            title=title,
            training_date=training_date,
            status=status,
            description=request.form.get("description"),
        )
        ids = [int(value) for value in request.form.getlist("attendee_ids") if value.isdigit()]
        row.attendees = Collaborator.query.filter(Collaborator.id.in_(ids)).all() if ids else []
        db.session.add(row)
        audit("crear_capacitacion", "Training", after={"title": row.title})
        db.session.commit()
        flash("Capacitación registrada.", "success")
        return redirect(url_for("hr.trainings"))

    return render_template(
        "hr/trainings.html",
        rows=Training.query.order_by(Training.training_date.desc()).all(),
        collaborators=collaborators,
    )


@bp.route("/collaborators/<int:collaborator_id>/document", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def upload_document(collaborator_id):
    collaborator = db.get_or_404(Collaborator, collaborator_id)
    file_storage = request.files.get("file")
    if not file_storage or not file_storage.filename:
        flash("Selecciona un archivo.", "danger")
        return redirect(url_for("hr.collaborator_detail", collaborator_id=collaborator.id))

    try:
        path = save_upload(file_storage, prefix=f"collab_{collaborator.id}")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("hr.collaborator_detail", collaborator_id=collaborator.id))

    document = CollaboratorDocument(
        collaborator_id=collaborator.id,
        document_type=(request.form.get("document_type") or "Documento").strip(),
        file_name=file_storage.filename,
        file_path=path,
        sensitive=bool(request.form.get("sensitive")),
    )
    db.session.add(document)
    audit("subir_documento_colaborador", "Collaborator", collaborator.id, after={"document_type": document.document_type})
    db.session.commit()
    flash("Documento cargado.", "success")
    return redirect(url_for("hr.collaborator_detail", collaborator_id=collaborator.id))


@bp.route("/attendance/<int:mark_id>/correct", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def correct_mark(mark_id):
    mark = db.get_or_404(AttendanceMark, mark_id)
    raw = request.form.get("marked_at")
    reason = (request.form.get("reason") or "").strip()
    mark_type = request.form.get("mark_type", mark.mark_type)

    if not raw or not reason:
        flash("La nueva fecha/hora y el motivo son obligatorios.", "danger")
        return redirect(url_for("hr.attendance"))
    if mark_type not in MARK_TYPES:
        flash("Tipo de marcación no válido.", "danger")
        return redirect(url_for("hr.attendance"))

    try:
        marked_at = datetime.fromisoformat(raw)
    except ValueError:
        flash("La fecha/hora indicada no es válida.", "danger")
        return redirect(url_for("hr.attendance"))

    before = {"marked_at": str(mark.marked_at), "type": mark.mark_type}
    mark.marked_at = marked_at
    mark.mark_type = mark_type
    mark.note = request.form.get("note", mark.note)
    mark.corrected = True
    audit(
        "corregir_marcacion",
        "AttendanceMark",
        mark.id,
        before=before,
        after={"marked_at": str(mark.marked_at), "type": mark.mark_type},
        reason=reason,
    )
    db.session.commit()
    flash("Marcación corregida con auditoría.", "success")
    return redirect(url_for("hr.attendance", date=mark.marked_at.date().isoformat()))


@bp.route("/collaborators/<int:collaborator_id>/vacation-adjust", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def vacation_adjust(collaborator_id):
    collaborator = db.get_or_404(Collaborator, collaborator_id)
    reason = (request.form.get("reason") or "").strip()
    try:
        days = Decimal(str(request.form.get("days") or 0))
    except InvalidOperation:
        days = Decimal("0")

    if days == 0 or not reason:
        flash("Indica una cantidad distinta de cero y el motivo del ajuste.", "danger")
        return redirect(url_for("hr.collaborator_detail", collaborator_id=collaborator.id))

    new_balance = Decimal(str(collaborator.vacation_balance or 0)) + days
    if new_balance < 0:
        flash("El ajuste dejaría un saldo de vacaciones negativo.", "danger")
        return redirect(url_for("hr.collaborator_detail", collaborator_id=collaborator.id))

    collaborator.vacation_balance = new_balance
    db.session.add(
        VacationMovement(
            collaborator_id=collaborator.id,
            movement_type="ajuste",
            days=days,
            effective_date=date.today(),
            note=reason,
            created_by_id=current_user.id,
        )
    )
    audit(
        "ajustar_vacaciones",
        "Collaborator",
        collaborator.id,
        after={"days": str(days), "balance": str(collaborator.vacation_balance)},
        reason=reason,
    )
    db.session.commit()
    flash("Saldo de vacaciones ajustado con trazabilidad.", "success")
    return redirect(url_for("hr.collaborator_detail", collaborator_id=collaborator.id))
