from datetime import datetime, date
from decimal import Decimal
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


def utcnow():
    return datetime.utcnow()


role_permissions = db.Table(
    "role_permissions",
    db.Column("role_id", db.Integer, db.ForeignKey("roles.id"), primary_key=True),
    db.Column("permission_id", db.Integer, db.ForeignKey("permissions.id"), primary_key=True),
)

project_members = db.Table(
    "project_members",
    db.Column("project_id", db.Integer, db.ForeignKey("projects.id"), primary_key=True),
    db.Column("collaborator_id", db.Integer, db.ForeignKey("collaborators.id"), primary_key=True),
)



training_attendees = db.Table(
    "training_attendees",
    db.Column("training_id", db.Integer, db.ForeignKey("trainings.id"), primary_key=True),
    db.Column("collaborator_id", db.Integer, db.ForeignKey("collaborators.id"), primary_key=True),
)

task_collaborators = db.Table(
    "task_collaborators",
    db.Column("task_id", db.Integer, db.ForeignKey("tasks.id"), primary_key=True),
    db.Column("collaborator_id", db.Integer, db.ForeignKey("collaborators.id"), primary_key=True),
)


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Role(db.Model, TimestampMixin):
    __tablename__ = "roles"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    label = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))
    active = db.Column(db.Boolean, default=True, nullable=False)
    permissions = db.relationship("Permission", secondary=role_permissions, back_populates="roles")


class Permission(db.Model):
    __tablename__ = "permissions"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(120), unique=True, nullable=False)
    label = db.Column(db.String(160), nullable=False)
    module = db.Column(db.String(80), nullable=False)
    roles = db.relationship("Role", secondary=role_permissions, back_populates="permissions")


class User(UserMixin, db.Model, TimestampMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(190), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    failed_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    role = db.relationship("Role", backref="users")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.active

    @property
    def is_superadmin(self):
        return bool(self.role and self.role.name == "superadmin")

    def has_permission(self, code):
        if self.is_superadmin:
            return True
        return bool(self.role and any(p.code == code for p in self.role.permissions))


class AdvisorProject(db.Model, TimestampMixin):
    __tablename__ = "advisor_projects"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.String(255))
    active = db.Column(db.Boolean, default=True, nullable=False)


class Collaborator(db.Model, TimestampMixin):
    __tablename__ = "collaborators"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    code = db.Column(db.String(40), unique=True, index=True)
    phone = db.Column(db.String(50))
    personal_email = db.Column(db.String(190))
    corporate_email = db.Column(db.String(190))
    emergency_contact = db.Column(db.String(255))
    job_title = db.Column(db.String(140), nullable=False, default="Colaborador")
    department = db.Column(db.String(140))
    advisor_project_id = db.Column(db.Integer, db.ForeignKey("advisor_projects.id"), index=True)
    supervisor_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    join_date = db.Column(db.Date, default=date.today)
    contract_type = db.Column(db.String(80))
    status = db.Column(db.String(30), default="activo", nullable=False)
    work_mode = db.Column(db.String(30), default="presencial")
    base_salary = db.Column(db.Numeric(12, 2), default=0)
    payment_method = db.Column(db.String(80))
    vacation_rate = db.Column(db.Numeric(8, 3), default=0)
    vacation_balance = db.Column(db.Numeric(8, 2), default=0)
    notes = db.Column(db.Text)
    user = db.relationship("User", backref=db.backref("collaborator", uselist=False))
    advisor_project = db.relationship("AdvisorProject", backref="collaborators")
    supervisor = db.relationship("Collaborator", remote_side=[id], backref="subordinates")


class WorkSchedule(db.Model, TimestampMixin):
    __tablename__ = "work_schedules"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    workdays = db.Column(db.String(50), default="0,1,2,3,4")
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    lunch_minutes = db.Column(db.Integer, default=60)
    break_minutes = db.Column(db.Integer, default=30)
    tolerance_minutes = db.Column(db.Integer, default=10)
    active = db.Column(db.Boolean, default=True)


class ScheduleAssignment(db.Model, TimestampMixin):
    __tablename__ = "schedule_assignments"
    id = db.Column(db.Integer, primary_key=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False)
    schedule_id = db.Column(db.Integer, db.ForeignKey("work_schedules.id"), nullable=False)
    starts_on = db.Column(db.Date, default=date.today)
    ends_on = db.Column(db.Date)
    collaborator = db.relationship("Collaborator", backref="schedule_assignments")
    schedule = db.relationship("WorkSchedule")


class AttendanceMark(db.Model):
    __tablename__ = "attendance_marks"
    id = db.Column(db.Integer, primary_key=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False, index=True)
    mark_type = db.Column(db.String(30), nullable=False)
    marked_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    note = db.Column(db.String(255))
    corrected = db.Column(db.Boolean, default=False)
    collaborator = db.relationship("Collaborator", backref="attendance_marks")


class LeaveRequest(db.Model, TimestampMixin):
    __tablename__ = "leave_requests"
    id = db.Column(db.Integer, primary_key=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False)
    leave_type = db.Column(db.String(40), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    days = db.Column(db.Numeric(8, 2), default=0)
    reason = db.Column(db.Text)
    status = db.Column(db.String(30), default="pendiente")
    approver_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    decided_at = db.Column(db.DateTime)
    collaborator = db.relationship("Collaborator", backref="leave_requests")
    approver = db.relationship("User")


class VacationMovement(db.Model):
    __tablename__ = "vacation_movements"
    id = db.Column(db.Integer, primary_key=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False)
    movement_type = db.Column(db.String(30), nullable=False)
    days = db.Column(db.Numeric(8, 2), nullable=False)
    effective_date = db.Column(db.Date, default=date.today)
    leave_request_id = db.Column(db.Integer, db.ForeignKey("leave_requests.id"))
    note = db.Column(db.String(255))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    collaborator = db.relationship("Collaborator", backref="vacation_movements")


class Evaluation(db.Model, TimestampMixin):
    __tablename__ = "evaluations"
    id = db.Column(db.Integer, primary_key=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False)
    evaluator_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    evaluation_date = db.Column(db.Date, default=date.today)
    score = db.Column(db.Numeric(5, 2), default=0)
    period = db.Column(db.String(80))
    notes = db.Column(db.Text)
    collaborator = db.relationship("Collaborator", backref="evaluations")
    evaluator = db.relationship("User")


class Training(db.Model, TimestampMixin):
    __tablename__ = "trainings"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    training_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(30), default="programada")
    description = db.Column(db.Text)
    attendees = db.relationship("Collaborator", secondary=training_attendees, backref="trainings")


class CollaboratorDocument(db.Model, TimestampMixin):
    __tablename__ = "collaborator_documents"
    id = db.Column(db.Integer, primary_key=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False)
    document_type = db.Column(db.String(100), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    sensitive = db.Column(db.Boolean, default=True)
    collaborator = db.relationship("Collaborator", backref="documents")


class Client(db.Model, TimestampMixin):
    __tablename__ = "clients"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, index=True)
    business_name = db.Column(db.String(180), nullable=False, index=True)
    contact_name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(60), index=True)
    other_phones = db.Column(db.Text)
    email = db.Column(db.String(190), index=True)
    preferred_channel = db.Column(db.String(40))
    industry = db.Column(db.String(120))
    services_offered = db.Column(db.Text)
    website = db.Column(db.String(255))
    facebook = db.Column(db.String(255))
    instagram = db.Column(db.String(255))
    other_social = db.Column(db.String(255))
    address = db.Column(db.String(255))
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    country = db.Column(db.String(100))
    timezone = db.Column(db.String(80))
    source = db.Column(db.String(80))
    owner_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), index=True)
    priority = db.Column(db.String(20), default="media")
    main_interest = db.Column(db.String(180))
    estimated_budget = db.Column(db.Numeric(12, 2))
    record_type = db.Column(db.String(30), default="seguimiento", nullable=False, index=True)
    pipeline_stage = db.Column(db.String(40), default="nuevo", nullable=False, index=True)
    client_status = db.Column(db.String(30), default="activo", index=True)
    lost_reason = db.Column(db.String(255))
    notes = db.Column(db.Text)
    owner = db.relationship("Collaborator", backref="owned_clients")

    @property
    def outstanding_balance(self):
        return sum(((r.total_amount or 0) - (r.paid_amount or 0) for r in self.receivables), 0)



class OwnershipHistory(db.Model):
    __tablename__ = "ownership_history"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    previous_owner_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    new_owner_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    reason = db.Column(db.String(255))
    changed_at = db.Column(db.DateTime, default=utcnow)
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    client = db.relationship("Client", backref="ownership_history")


class ClientCollaborator(db.Model, TimestampMixin):
    __tablename__ = "client_collaborators"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False, index=True)
    role_in_client = db.Column(db.String(120))
    primary = db.Column(db.Boolean, default=False)
    client = db.relationship("Client", backref="collaborator_assignments")
    collaborator = db.relationship("Collaborator", backref="client_assignments")
    __table_args__ = (db.UniqueConstraint("client_id", "collaborator_id", name="uq_client_collaborator"),)


class Interaction(db.Model, TimestampMixin):
    __tablename__ = "interactions"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    interaction_type = db.Column(db.String(40), nullable=False)
    occurred_at = db.Column(db.DateTime, default=utcnow)
    subject = db.Column(db.String(180))
    notes = db.Column(db.Text)
    result = db.Column(db.String(180))
    next_followup_at = db.Column(db.DateTime)
    client = db.relationship("Client", backref="interactions")
    user = db.relationship("User")


class ClientComment(db.Model):
    __tablename__ = "client_comments"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    process_type = db.Column(db.String(80), nullable=False, default="Seguimiento")
    process_detail = db.Column(db.String(180))
    department_snapshot = db.Column(db.String(140))
    advisor_project_snapshot = db.Column(db.String(120))
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    client = db.relationship("Client", backref=db.backref("comments", order_by="ClientComment.created_at.desc()"))
    user = db.relationship("User")


class ProductService(db.Model, TimestampMixin):
    __tablename__ = "product_services"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), unique=True, nullable=False)
    category = db.Column(db.String(80), default="servicio")
    description = db.Column(db.Text)
    base_price = db.Column(db.Numeric(12, 2))
    currency = db.Column(db.String(8), default="USD")
    duration_months = db.Column(db.Integer)
    modality = db.Column(db.String(40))
    maintenance = db.Column(db.String(40), default="no_aplica")
    responsible_area = db.Column(db.String(120))
    renewal_required = db.Column(db.Boolean, default=False)
    is_physical = db.Column(db.Boolean, default=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    components = db.Column(db.Text)


class ClientContract(db.Model, TimestampMixin):
    __tablename__ = "client_contracts"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product_services.id"), nullable=False)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"))
    status = db.Column(db.String(30), default="activo", nullable=False, index=True)
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date)
    agreed_price = db.Column(db.Numeric(12, 2))
    principal = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    client = db.relationship("Client", backref="contracts")
    product = db.relationship("ProductService")


class SalesGoal(db.Model, TimestampMixin):
    __tablename__ = "sales_goals"
    id = db.Column(db.Integer, primary_key=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    target_amount = db.Column(db.Numeric(12, 2), default=0)
    target_sales = db.Column(db.Integer, default=0)
    collaborator = db.relationship("Collaborator", backref="sales_goals")


class Quote(db.Model, TimestampMixin):
    __tablename__ = "quotes"
    id = db.Column(db.Integer, primary_key=True)
    quote_no = db.Column(db.String(40), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    advisor_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    version = db.Column(db.Integer, default=1)
    status = db.Column(db.String(30), default="borrador")
    currency = db.Column(db.String(8), default="USD")
    subtotal = db.Column(db.Numeric(12, 2), default=0)
    discount = db.Column(db.Numeric(12, 2), default=0)
    total = db.Column(db.Numeric(12, 2), default=0)
    valid_until = db.Column(db.Date)
    notes = db.Column(db.Text)
    client = db.relationship("Client", backref="quotes")
    advisor = db.relationship("Collaborator")


class QuoteItem(db.Model):
    __tablename__ = "quote_items"
    id = db.Column(db.Integer, primary_key=True)
    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product_services.id"))
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Numeric(10, 2), default=1)
    unit_price = db.Column(db.Numeric(12, 2), default=0)
    total = db.Column(db.Numeric(12, 2), default=0)
    quote = db.relationship("Quote", backref=db.backref("items", cascade="all, delete-orphan"))
    product = db.relationship("ProductService")


class Sale(db.Model, TimestampMixin):
    __tablename__ = "sales"
    id = db.Column(db.Integer, primary_key=True)
    sale_no = db.Column(db.String(40), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    advisor_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), index=True)
    quote_id = db.Column(db.Integer, db.ForeignKey("quotes.id"))
    sale_date = db.Column(db.Date, default=date.today)
    status = db.Column(db.String(30), default="confirmada")
    currency = db.Column(db.String(8), default="USD")
    total = db.Column(db.Numeric(12, 2), default=0)
    amount_paid = db.Column(db.Numeric(12, 2), default=0)
    balance = db.Column(db.Numeric(12, 2), default=0)
    notes = db.Column(db.Text)
    client = db.relationship("Client", backref="sales")
    advisor = db.relationship("Collaborator")
    quote = db.relationship("Quote", backref="sale", uselist=False)


class SaleItem(db.Model):
    __tablename__ = "sale_items"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product_services.id"))
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Numeric(10, 2), default=1)
    list_price = db.Column(db.Numeric(12, 2), default=0)
    discount = db.Column(db.Numeric(12, 2), default=0)
    unit_price = db.Column(db.Numeric(12, 2), default=0)
    total = db.Column(db.Numeric(12, 2), default=0)
    sale = db.relationship("Sale", backref=db.backref("items", cascade="all, delete-orphan"))
    product = db.relationship("ProductService")


class Payment(db.Model, TimestampMixin):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, index=True)
    effective_date = db.Column(db.Date, default=date.today)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(8), default="USD")
    method = db.Column(db.String(50), default="transferencia")
    reference = db.Column(db.String(120))
    proof_path = db.Column(db.String(255))
    status = db.Column(db.String(30), default="confirmado")
    notes = db.Column(db.Text)
    registered_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    sale = db.relationship("Sale", backref="payments")
    client = db.relationship("Client", backref="payments")


class Deduction(db.Model, TimestampMixin):
    __tablename__ = "deductions"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, index=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"))
    concept = db.Column(db.String(160), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    affects_commission = db.Column(db.Boolean, default=True)
    notes = db.Column(db.Text)
    sale = db.relationship("Sale", backref="deductions")


class CommissionRule(db.Model, TimestampMixin):
    __tablename__ = "commission_rules"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product_services.id"))
    threshold_min = db.Column(db.Numeric(12, 2), default=0)
    threshold_max = db.Column(db.Numeric(12, 2))
    percentage = db.Column(db.Numeric(8, 3), default=0)
    fixed_amount = db.Column(db.Numeric(12, 2), default=0)
    minimum_commission = db.Column(db.Numeric(12, 2), default=0)
    trigger = db.Column(db.String(30), default="paid")
    active = db.Column(db.Boolean, default=True)
    product = db.relationship("ProductService")


class Commission(db.Model, TimestampMixin):
    __tablename__ = "commissions"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, unique=True)
    advisor_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False, index=True)
    rule_id = db.Column(db.Integer, db.ForeignKey("commission_rules.id"))
    base_amount = db.Column(db.Numeric(12, 2), default=0)
    percentage = db.Column(db.Numeric(8, 3), default=0)
    minimum_applied = db.Column(db.Boolean, default=False)
    amount = db.Column(db.Numeric(12, 2), default=0)
    status = db.Column(db.String(30), default="estimada")
    sale = db.relationship("Sale", backref=db.backref("commission", uselist=False))
    advisor = db.relationship("Collaborator")
    rule = db.relationship("CommissionRule")


class AccountReceivable(db.Model, TimestampMixin):
    __tablename__ = "accounts_receivable"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, unique=True)
    total_amount = db.Column(db.Numeric(12, 2), default=0)
    paid_amount = db.Column(db.Numeric(12, 2), default=0)
    due_date = db.Column(db.Date)
    promise_date = db.Column(db.Date)
    status = db.Column(db.String(30), default="al_dia")
    notes = db.Column(db.Text)
    client = db.relationship("Client", backref="receivables")
    sale = db.relationship("Sale", backref=db.backref("receivable", uselist=False))


class Income(db.Model, TimestampMixin):
    __tablename__ = "incomes"
    id = db.Column(db.Integer, primary_key=True)
    effective_date = db.Column(db.Date, default=date.today)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"))
    concept = db.Column(db.String(180), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(8), default="USD")
    method = db.Column(db.String(60))
    reference = db.Column(db.String(120))
    advisor_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    status = db.Column(db.String(30), default="confirmado")
    notes = db.Column(db.Text)


class Expense(db.Model, TimestampMixin):
    __tablename__ = "expenses"
    id = db.Column(db.Integer, primary_key=True)
    expense_date = db.Column(db.Date, default=date.today)
    category = db.Column(db.String(100), nullable=False)
    beneficiary = db.Column(db.String(180))
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(8), default="USD")
    method = db.Column(db.String(60))
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"))
    proof_path = db.Column(db.String(255))
    status = db.Column(db.String(30), default="pagado")
    recurring = db.Column(db.Boolean, default=False)
    next_due_date = db.Column(db.Date)
    notes = db.Column(db.Text)


class Payable(db.Model, TimestampMixin):
    __tablename__ = "payables"
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(180), nullable=False)
    concept = db.Column(db.String(180), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    paid_amount = db.Column(db.Numeric(12, 2), default=0)
    currency = db.Column(db.String(8), default="USD")
    due_date = db.Column(db.Date)
    status = db.Column(db.String(30), default="pendiente")
    recurring = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)


class PayrollPeriod(db.Model, TimestampMixin):
    __tablename__ = "payroll_periods"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(30), default="borrador")
    paid_at = db.Column(db.DateTime)


class PayrollLine(db.Model):
    __tablename__ = "payroll_lines"
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey("payroll_periods.id"), nullable=False)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False)
    base_salary = db.Column(db.Numeric(12, 2), default=0)
    commissions = db.Column(db.Numeric(12, 2), default=0)
    bonuses = db.Column(db.Numeric(12, 2), default=0)
    deductions = db.Column(db.Numeric(12, 2), default=0)
    total = db.Column(db.Numeric(12, 2), default=0)
    notes = db.Column(db.Text)
    period = db.relationship("PayrollPeriod", backref=db.backref("lines", cascade="all, delete-orphan"))
    collaborator = db.relationship("Collaborator")


class TaskTemplate(db.Model, TimestampMixin):
    __tablename__ = "task_templates"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product_services.id"))
    name = db.Column(db.String(160), nullable=False)
    active = db.Column(db.Boolean, default=True)
    product = db.relationship("ProductService", backref="task_templates")


class TaskTemplateItem(db.Model):
    __tablename__ = "task_template_items"
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("task_templates.id"), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text)
    task_type = db.Column(db.String(60), default="cliente")
    priority = db.Column(db.String(20), default="media")
    due_days = db.Column(db.Integer, default=3)
    required = db.Column(db.Boolean, default=True)
    template = db.relationship("TaskTemplate", backref=db.backref("items", cascade="all, delete-orphan"))


class Project(db.Model, TimestampMixin):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    project_no = db.Column(db.String(40), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"))
    product_id = db.Column(db.Integer, db.ForeignKey("product_services.id"))
    name = db.Column(db.String(180), nullable=False)
    coordinator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    department = db.Column(db.String(120))
    status = db.Column(db.String(50), default="pendiente_onboarding", index=True)
    progress = db.Column(db.Integer, default=0)
    starts_on = db.Column(db.Date)
    due_on = db.Column(db.Date)
    completed_on = db.Column(db.Date)
    notes = db.Column(db.Text)
    client = db.relationship("Client", backref="projects")
    product = db.relationship("ProductService")
    coordinator = db.relationship("Collaborator", foreign_keys=[coordinator_id])
    members = db.relationship("Collaborator", secondary=project_members, backref="projects_member")


class Task(db.Model, TimestampMixin):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text)
    task_type = db.Column(db.String(60), default="interna")
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"))
    assignee_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    priority = db.Column(db.String(20), default="media")
    status = db.Column(db.String(30), default="pendiente", index=True)
    starts_at = db.Column(db.DateTime)
    due_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    checklist = db.Column(db.Text)
    estimated_minutes = db.Column(db.Integer)
    spent_minutes = db.Column(db.Integer, default=0)
    recurring = db.Column(db.Boolean, default=False)
    client = db.relationship("Client", backref="tasks")
    project = db.relationship("Project", backref="tasks")
    assignee = db.relationship("Collaborator", foreign_keys=[assignee_id])
    collaborators = db.relationship("Collaborator", secondary=task_collaborators, backref="shared_tasks")


class TaskComment(db.Model):
    __tablename__ = "task_comments"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    task = db.relationship("Task", backref="comments")
    user = db.relationship("User")


class ChangeRequest(db.Model, TimestampMixin):
    __tablename__ = "change_requests"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.String(20), default="media")
    responsible_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    scope_class = db.Column(db.String(30), default="incluida")
    status = db.Column(db.String(30), default="recibida")
    project = db.relationship("Project", backref="change_requests")


class SupportTicket(db.Model, TimestampMixin):
    __tablename__ = "support_tickets"
    id = db.Column(db.Integer, primary_key=True)
    ticket_no = db.Column(db.String(40), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    ticket_type = db.Column(db.String(60), nullable=False)
    channel = db.Column(db.String(40), default="interno")
    priority = db.Column(db.String(20), default="normal")
    responsible_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    status = db.Column(db.String(30), default="nuevo")
    subject = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text)
    first_response_due = db.Column(db.DateTime)
    resolution_due = db.Column(db.DateTime)
    resolved_at = db.Column(db.DateTime)
    client = db.relationship("Client", backref="tickets")
    responsible = db.relationship("Collaborator")


class TicketComment(db.Model):
    __tablename__ = "ticket_comments"
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("support_tickets.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    ticket = db.relationship("SupportTicket", backref="comments")
    user = db.relationship("User")


class PrintOrder(db.Model, TimestampMixin):
    __tablename__ = "print_orders"
    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(40), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"))
    related_order_id = db.Column(db.Integer, db.ForeignKey("print_orders.id"))
    responsible_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"))
    provider = db.Column(db.String(180))
    status = db.Column(db.String(50), default="diseno")
    shipping_address = db.Column(db.String(255))
    total_cost = db.Column(db.Numeric(12, 2), default=0)
    total_sale = db.Column(db.Numeric(12, 2), default=0)
    notes = db.Column(db.Text)
    client = db.relationship("Client", backref="print_orders")
    responsible = db.relationship("Collaborator")


class PrintItem(db.Model, TimestampMixin):
    __tablename__ = "print_items"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("print_orders.id"), nullable=False)
    name = db.Column(db.String(180), nullable=False)
    specification = db.Column(db.Text)
    quantity = db.Column(db.Integer, default=1)
    design_status = db.Column(db.String(40), default="pendiente")
    design_approved_at = db.Column(db.DateTime)
    shipping_approved_at = db.Column(db.DateTime)
    production_status = db.Column(db.String(40), default="pendiente")
    qty_sent = db.Column(db.Integer, default=0)
    qty_received = db.Column(db.Integer, default=0)
    order = db.relationship("PrintOrder", backref=db.backref("items", cascade="all, delete-orphan"))


class DesignVersion(db.Model):
    __tablename__ = "design_versions"
    id = db.Column(db.Integer, primary_key=True)
    print_item_id = db.Column(db.Integer, db.ForeignKey("print_items.id"), nullable=False)
    version = db.Column(db.Integer, default=1)
    file_path = db.Column(db.String(255))
    status = db.Column(db.String(40), default="revision")
    comment = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow)
    print_item = db.relationship("PrintItem", backref="design_versions")


class Shipment(db.Model, TimestampMixin):
    __tablename__ = "shipments"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("print_orders.id"), nullable=False)
    carrier = db.Column(db.String(120))
    tracking_number = db.Column(db.String(160))
    local_delivery = db.Column(db.Boolean, default=False)
    shipped_at = db.Column(db.DateTime)
    estimated_delivery = db.Column(db.Date)
    received_at = db.Column(db.DateTime)
    status = db.Column(db.String(40), default="pendiente")
    proof_path = db.Column(db.String(255))
    notes = db.Column(db.Text)
    order = db.relationship("PrintOrder", backref="shipments")


class ShipmentItem(db.Model):
    __tablename__ = "shipment_items"
    id = db.Column(db.Integer, primary_key=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("shipments.id"), nullable=False)
    print_item_id = db.Column(db.Integer, db.ForeignKey("print_items.id"), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    received_quantity = db.Column(db.Integer, default=0)
    shipment = db.relationship("Shipment", backref="shipment_items")
    print_item = db.relationship("PrintItem")


class PrintIncident(db.Model, TimestampMixin):
    __tablename__ = "print_incidents"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("print_orders.id"), nullable=False)
    print_item_id = db.Column(db.Integer, db.ForeignKey("print_items.id"))
    shipment_id = db.Column(db.Integer, db.ForeignKey("shipments.id"))
    incident_type = db.Column(db.String(80), nullable=False)
    status = db.Column(db.String(30), default="abierta")
    description = db.Column(db.Text, nullable=False)
    resolution = db.Column(db.Text)
    order = db.relationship("PrintOrder", backref="incidents")


class Renewal(db.Model, TimestampMixin):
    __tablename__ = "renewals"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    contract_id = db.Column(db.Integer, db.ForeignKey("client_contracts.id"))
    renewal_type = db.Column(db.String(80), nullable=False)
    due_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(30), default="pendiente")
    last_contact_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    client = db.relationship("Client", backref="renewals")
    contract = db.relationship("ClientContract")


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    message = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(255))
    priority = db.Column(db.String(20), default="normal")
    read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    user = db.relationship("User", backref="notifications")


class Attachment(db.Model):
    __tablename__ = "attachments"
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(60), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(60), nullable=False)
    entity = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(db.String(60))
    before_json = db.Column(db.Text)
    after_json = db.Column(db.Text)
    reason = db.Column(db.String(255))
    ip_address = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    user = db.relationship("User")


class CatalogItem(db.Model, TimestampMixin):
    __tablename__ = "catalog_items"
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(80), nullable=False, index=True)
    code = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(160), nullable=False)
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    __table_args__ = (db.UniqueConstraint("category", "code", name="uq_catalog_category_code"),)


class SystemSetting(db.Model, TimestampMixin):
    __tablename__ = "system_settings"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(120), unique=True, nullable=False)
    value = db.Column(db.Text)
    description = db.Column(db.String(255))


class Holiday(db.Model):
    __tablename__ = "holidays"
    id = db.Column(db.Integer, primary_key=True)
    holiday_date = db.Column(db.Date, unique=True, nullable=False)
    name = db.Column(db.String(160), nullable=False)
    non_working = db.Column(db.Boolean, default=True)


class PasswordReset(db.Model):
    __tablename__ = "password_resets"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    token_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)
