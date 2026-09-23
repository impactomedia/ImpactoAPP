from datetime import date, datetime, timedelta, time
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import roles_required
from app.helpers import audit, next_code, save_upload
from app.models import Collaborator, User, Role, WorkSchedule, ScheduleAssignment, AttendanceMark, LeaveRequest, VacationMovement, Evaluation, Training, CollaboratorDocument, AdvisorProject

bp = Blueprint("hr", __name__, url_prefix="/hr")

MARK_TYPES = ["entrada", "break_inicio", "break_fin", "almuerzo_inicio", "almuerzo_fin", "salida"]


@bp.route("/")
@login_required
def dashboard():
    today = date.today()
    collaborators = Collaborator.query.order_by(Collaborator.status, Collaborator.id).all()
    today_marks = AttendanceMark.query.filter(db.func.date(AttendanceMark.marked_at) == today).order_by(AttendanceMark.marked_at.desc()).all()
    leaves = LeaveRequest.query.filter(LeaveRequest.start_date <= today, LeaveRequest.end_date >= today, LeaveRequest.status == "aprobada").all()
    return render_template("hr/dashboard.html", collaborators=collaborators, today_marks=today_marks, leaves=leaves)


@bp.route("/collaborators", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def collaborators():
    roles = Role.query.filter_by(active=True).all()
    supervisors = Collaborator.query.filter_by(status="activo").all()
    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()
    selected_project_id = request.args.get("advisor_project_id", type=int)
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if User.query.filter_by(email=email).first():
            flash("Ya existe un usuario con ese correo.", "danger")
            return redirect(url_for("hr.collaborators"))
        role = db.session.get(Role, request.form.get("role_id", type=int))
        advisor_project_id = request.form.get("advisor_project_id", type=int)
        if role and role.name == "advisor" and not advisor_project_id:
            flash("Los asesores deben pertenecer al proyecto IMPACTO o NOVAX.", "warning")
            return render_template("hr/collaborators.html", collaborators=Collaborator.query.order_by(Collaborator.id.desc()).all(), roles=roles, supervisors=supervisors, advisor_projects=advisor_projects, selected_project_id=selected_project_id)
        user = User(name=request.form.get("name", "").strip(), email=email, role=role, active=True)
        user.set_password(request.form.get("password") or "Impacto123!")
        db.session.add(user)
        db.session.flush()
        c = Collaborator(
            user_id=user.id,
            code=next_code("COL", Collaborator),
            phone=request.form.get("phone"),
            corporate_email=email,
            job_title=request.form.get("job_title", "Colaborador"),
            department=request.form.get("department"),
            advisor_project_id=advisor_project_id,
            supervisor_id=request.form.get("supervisor_id", type=int),
            join_date=date.fromisoformat(request.form.get("join_date")) if request.form.get("join_date") else date.today(),
            contract_type=request.form.get("contract_type"),
            status="activo",
            work_mode=request.form.get("work_mode", "presencial"),
            base_salary=request.form.get("base_salary") or 0,
            vacation_rate=request.form.get("vacation_rate") or 0,
            vacation_balance=request.form.get("vacation_balance") or 0,
        )
        db.session.add(c)
        audit("crear_colaborador", "Collaborator", after={"email": email, "job_title": c.job_title})
        db.session.commit()
        flash("Colaborador creado.", "success")
        return redirect(url_for("hr.collaborators"))
    q = Collaborator.query
    if selected_project_id:
        q = q.filter(Collaborator.advisor_project_id == selected_project_id)
    rows = q.order_by(Collaborator.id.desc()).all()
    return render_template("hr/collaborators.html", collaborators=rows, roles=roles, supervisors=supervisors, advisor_projects=advisor_projects, selected_project_id=selected_project_id)


@bp.route("/collaborators/<int:collaborator_id>", methods=["GET", "POST"])
@login_required
def collaborator_detail(collaborator_id):
    c = db.get_or_404(Collaborator, collaborator_id)
    advisor_projects = AdvisorProject.query.filter_by(active=True).order_by(AdvisorProject.name).all()
    if request.method == "POST" and (current_user.is_superadmin or current_user.role.name in {"admin", "manager", "hr"}):
        c.job_title = request.form.get("job_title", c.job_title)
        c.department = request.form.get("department", c.department)
        c.advisor_project_id = request.form.get("advisor_project_id", type=int)
        c.status = request.form.get("status", c.status)
        c.base_salary = request.form.get("base_salary") or c.base_salary
        c.vacation_rate = request.form.get("vacation_rate") or c.vacation_rate
        c.vacation_balance = request.form.get("vacation_balance") or c.vacation_balance
        audit("editar_colaborador", "Collaborator", c.id)
        db.session.commit()
        flash("Expediente actualizado.", "success")
    return render_template("hr/collaborator_detail.html", collaborator=c, advisor_projects=advisor_projects)


@bp.route("/schedules", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def schedules():
    if request.method == "POST":
        s = WorkSchedule(
            name=request.form.get("name", "Horario"),
            workdays=request.form.get("workdays", "0,1,2,3,4"),
            start_time=time.fromisoformat(request.form.get("start_time")) if request.form.get("start_time") else None,
            end_time=time.fromisoformat(request.form.get("end_time")) if request.form.get("end_time") else None,
            lunch_minutes=request.form.get("lunch_minutes", type=int) or 60,
            break_minutes=request.form.get("break_minutes", type=int) or 30,
            tolerance_minutes=request.form.get("tolerance_minutes", type=int) or 10,
        )
        db.session.add(s)
        db.session.commit()
        flash("Horario creado.", "success")
        return redirect(url_for("hr.schedules"))
    return render_template("hr/schedules.html", schedules=WorkSchedule.query.order_by(WorkSchedule.id.desc()).all(), collaborators=Collaborator.query.filter_by(status="activo").all())


@bp.route("/schedules/assign", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def assign_schedule():
    a = ScheduleAssignment(collaborator_id=request.form.get("collaborator_id", type=int), schedule_id=request.form.get("schedule_id", type=int), starts_on=date.fromisoformat(request.form.get("starts_on")) if request.form.get("starts_on") else date.today(), ends_on=date.fromisoformat(request.form.get("ends_on")) if request.form.get("ends_on") else None)
    db.session.add(a)
    db.session.commit()
    flash("Horario asignado.", "success")
    return redirect(url_for("hr.schedules"))


@bp.route("/my-day")
@login_required
def my_day():
    c = current_user.collaborator
    if not c:
        flash("Tu usuario no tiene expediente de colaborador.", "warning")
        return redirect(url_for("dashboard.index"))
    today = date.today()
    marks = AttendanceMark.query.filter(AttendanceMark.collaborator_id == c.id, db.func.date(AttendanceMark.marked_at) == today).order_by(AttendanceMark.marked_at).all()
    last = marks[-1].mark_type if marks else None
    allowed = {
        None: ["entrada"],
        "entrada": ["break_inicio", "almuerzo_inicio", "salida"],
        "break_inicio": ["break_fin"],
        "break_fin": ["break_inicio", "almuerzo_inicio", "salida"],
        "almuerzo_inicio": ["almuerzo_fin"],
        "almuerzo_fin": ["break_inicio", "salida"],
        "salida": [],
    }.get(last, [])
    return render_template("hr/my_day.html", collaborator=c, marks=marks, allowed=allowed)


@bp.route("/my-day/mark", methods=["POST"])
@login_required
def mark():
    c = current_user.collaborator
    if not c:
        flash("No tienes expediente de colaborador.", "danger")
        return redirect(url_for("dashboard.index"))
    mark_type = request.form.get("mark_type")
    if mark_type not in MARK_TYPES:
        flash("Marcación inválida.", "danger")
    else:
        m = AttendanceMark(collaborator_id=c.id, mark_type=mark_type, note=request.form.get("note"))
        db.session.add(m)
        audit("marcacion", "AttendanceMark", after={"collaborator_id": c.id, "type": mark_type})
        db.session.commit()
        flash("Marcación registrada.", "success")
    return redirect(url_for("hr.my_day"))


@bp.route("/attendance")
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def attendance():
    selected = request.args.get("date")
    day = date.fromisoformat(selected) if selected else date.today()
    marks = AttendanceMark.query.filter(db.func.date(AttendanceMark.marked_at) == day).order_by(AttendanceMark.marked_at).all()
    return render_template("hr/attendance.html", marks=marks, day=day)


@bp.route("/leave", methods=["GET", "POST"])
@login_required
def leave():
    c = current_user.collaborator
    if request.method == "POST":
        if not c:
            flash("No tienes expediente de colaborador.", "danger")
            return redirect(url_for("hr.leave"))
        start = date.fromisoformat(request.form.get("start_date"))
        end = date.fromisoformat(request.form.get("end_date"))
        days = Decimal((end - start).days + 1)
        leave_type = request.form.get("leave_type", "vacaciones")
        if end < start:
            flash("La fecha final no puede ser anterior a la inicial.", "danger")
            return redirect(url_for("hr.leave"))
        if leave_type == "vacaciones" and Decimal(str(c.vacation_balance or 0)) < days:
            flash("El saldo disponible de vacaciones no cubre la solicitud.", "danger")
            return redirect(url_for("hr.leave"))
        row = LeaveRequest(collaborator_id=c.id, leave_type=leave_type, start_date=start, end_date=end, days=days, reason=request.form.get("reason"), status="pendiente")
        db.session.add(row)
        audit("solicitar_ausencia", "LeaveRequest", after={"days": str(days), "type": row.leave_type})
        db.session.commit()
        flash("Solicitud enviada.", "success")
        return redirect(url_for("hr.leave"))
    rows = LeaveRequest.query.filter_by(collaborator_id=c.id).order_by(LeaveRequest.created_at.desc()).all() if c else []
    return render_template("hr/leave.html", rows=rows, collaborator=c)


@bp.route("/leave/admin")
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def leave_admin():
    rows = LeaveRequest.query.order_by(LeaveRequest.created_at.desc()).all()
    return render_template("hr/leave_admin.html", rows=rows)


@bp.route("/leave/<int:leave_id>/<decision>", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def leave_decision(leave_id, decision):
    row = db.get_or_404(LeaveRequest, leave_id)
    if decision not in {"aprobar", "rechazar", "cancelar"}:
        flash("Decisión inválida.", "danger")
        return redirect(url_for("hr.leave_admin"))
    if decision == "aprobar":
        row.status = "aprobada"
        row.approver_id = current_user.id
        row.decided_at = datetime.utcnow()
        if row.leave_type == "vacaciones":
            c = row.collaborator
            c.vacation_balance = Decimal(str(c.vacation_balance or 0)) - Decimal(str(row.days or 0))
            db.session.add(VacationMovement(collaborator_id=c.id, movement_type="uso", days=-Decimal(str(row.days or 0)), effective_date=row.start_date, leave_request_id=row.id, note="Vacaciones aprobadas", created_by_id=current_user.id))
    elif decision == "rechazar":
        row.status = "rechazada"
        row.approver_id = current_user.id
        row.decided_at = datetime.utcnow()
    else:
        if row.status == "aprobada" and row.leave_type == "vacaciones":
            c = row.collaborator
            c.vacation_balance = Decimal(str(c.vacation_balance or 0)) + Decimal(str(row.days or 0))
            db.session.add(VacationMovement(collaborator_id=c.id, movement_type="reversion", days=Decimal(str(row.days or 0)), effective_date=date.today(), leave_request_id=row.id, note="Cancelación de vacaciones aprobadas", created_by_id=current_user.id))
        row.status = "cancelada"
    audit("decision_ausencia", "LeaveRequest", row.id, after={"status": row.status})
    db.session.commit()
    flash("Solicitud actualizada.", "success")
    return redirect(url_for("hr.leave_admin"))


@bp.route("/evaluations", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def evaluations():
    collaborators = Collaborator.query.filter_by(status="activo").all()
    if request.method == "POST":
        row = Evaluation(
            collaborator_id=request.form.get("collaborator_id", type=int),
            evaluator_id=current_user.id,
            evaluation_date=date.fromisoformat(request.form.get("evaluation_date")) if request.form.get("evaluation_date") else date.today(),
            score=request.form.get("score") or 0,
            period=request.form.get("period"),
            notes=request.form.get("notes"),
        )
        db.session.add(row)
        audit("evaluacion", "Evaluation", after={"collaborator_id": row.collaborator_id, "score": str(row.score)})
        db.session.commit()
        flash("Evaluación registrada.", "success")
        return redirect(url_for("hr.evaluations"))
    return render_template("hr/evaluations.html", rows=Evaluation.query.order_by(Evaluation.evaluation_date.desc()).all(), collaborators=collaborators)


@bp.route("/trainings", methods=["GET", "POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def trainings():
    collaborators = Collaborator.query.filter_by(status="activo").all()
    if request.method == "POST":
        row = Training(
            title=request.form.get("title", "Capacitación"),
            training_date=date.fromisoformat(request.form.get("training_date")),
            status=request.form.get("status", "programada"),
            description=request.form.get("description"),
        )
        ids = [int(x) for x in request.form.getlist("attendee_ids") if x.isdigit()]
        row.attendees = Collaborator.query.filter(Collaborator.id.in_(ids)).all() if ids else []
        db.session.add(row)
        audit("crear_capacitacion", "Training", after={"title": row.title})
        db.session.commit()
        flash("Capacitación registrada.", "success")
        return redirect(url_for("hr.trainings"))
    return render_template("hr/trainings.html", rows=Training.query.order_by(Training.training_date.desc()).all(), collaborators=collaborators)


@bp.route("/collaborators/<int:collaborator_id>/document", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def upload_document(collaborator_id):
    c = db.get_or_404(Collaborator, collaborator_id)
    f = request.files.get("file")
    if not f:
        flash("Selecciona un archivo.", "danger")
        return redirect(url_for("hr.collaborator_detail", collaborator_id=c.id))
    try:
        path = save_upload(f, prefix=f"collab_{c.id}")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("hr.collaborator_detail", collaborator_id=c.id))
    row = CollaboratorDocument(collaborator_id=c.id, document_type=request.form.get("document_type", "Documento"), file_name=f.filename, file_path=path, sensitive=bool(request.form.get("sensitive")))
    db.session.add(row)
    audit("subir_documento_colaborador", "Collaborator", c.id, after={"document_type": row.document_type})
    db.session.commit()
    flash("Documento cargado.", "success")
    return redirect(url_for("hr.collaborator_detail", collaborator_id=c.id))


@bp.route("/attendance/<int:mark_id>/correct", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr", "supervisor")
def correct_mark(mark_id):
    mark = db.get_or_404(AttendanceMark, mark_id)
    raw = request.form.get("marked_at")
    if not raw:
        flash("Indica la nueva fecha/hora.", "danger")
        return redirect(url_for("hr.attendance"))
    before = {"marked_at": str(mark.marked_at), "type": mark.mark_type}
    mark.marked_at = datetime.fromisoformat(raw)
    mark.mark_type = request.form.get("mark_type", mark.mark_type)
    mark.note = request.form.get("note", mark.note)
    mark.corrected = True
    audit("corregir_marcacion", "AttendanceMark", mark.id, before=before, after={"marked_at": str(mark.marked_at), "type": mark.mark_type}, reason=request.form.get("reason"))
    db.session.commit()
    flash("Marcación corregida con auditoría.", "success")
    return redirect(url_for("hr.attendance", date=mark.marked_at.date().isoformat()))


@bp.route("/collaborators/<int:collaborator_id>/vacation-adjust", methods=["POST"])
@login_required
@roles_required("superadmin", "admin", "manager", "hr")
def vacation_adjust(collaborator_id):
    c = db.get_or_404(Collaborator, collaborator_id)
    days = Decimal(str(request.form.get("days") or 0))
    c.vacation_balance = Decimal(str(c.vacation_balance or 0)) + days
    db.session.add(VacationMovement(collaborator_id=c.id, movement_type="ajuste", days=days, effective_date=date.today(), note=request.form.get("reason"), created_by_id=current_user.id))
    audit("ajustar_vacaciones", "Collaborator", c.id, after={"days": str(days), "balance": str(c.vacation_balance)}, reason=request.form.get("reason"))
    db.session.commit()
    flash("Saldo de vacaciones ajustado con trazabilidad.", "success")
    return redirect(url_for("hr.collaborator_detail", collaborator_id=c.id))
