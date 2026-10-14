"""Registros financieros quincenales. Tablas nuevas: no alteran ventas ni la contabilidad anterior."""
from datetime import date, datetime

from app.extensions import db


class AdvisorFortnightGoal(db.Model):
    __tablename__ = "advisor_fortnight_goals"
    id = db.Column(db.Integer, primary_key=True)
    advisor_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("advisor_projects.id"), nullable=False)
    period_start = db.Column(db.Date, nullable=False, index=True)
    total_goal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    net_goal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    percentage = db.Column(db.Numeric(7, 3), nullable=False, default=0)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("advisor_id", "period_start", name="uq_finance_advisor_fortnight"),)
    advisor = db.relationship("Collaborator", foreign_keys=[advisor_id])
    project = db.relationship("AdvisorProject")


class FinanceProjectAllocation(db.Model):
    """Asocia movimientos contables anteriores a un proyecto comercial sin cambiar su tabla."""
    __tablename__ = "finance_project_allocations"
    id = db.Column(db.Integer, primary_key=True)
    record_type = db.Column(db.String(20), nullable=False)
    record_id = db.Column(db.Integer, nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("advisor_projects.id"), nullable=False)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("record_type", "record_id", name="uq_finance_record_allocation"),)
    project = db.relationship("AdvisorProject")


class FinanceProjectMovement(db.Model):
    """Egreso ejecutado con trazabilidad; ninguna solicitud pendiente afecta los totales."""
    __tablename__ = "finance_project_movements"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("advisor_projects.id"), nullable=False, index=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), index=True)
    category = db.Column(db.String(35), nullable=False)  # imprenta, meta_ads, dominio, otros
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(8), nullable=False, default="USD")
    requested_on = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    authorized_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    authorized_at = db.Column(db.DateTime)
    authorization_reason = db.Column(db.Text)
    executed_on = db.Column(db.Date)
    executed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    status = db.Column(db.String(30), nullable=False, default="pendiente")
    notes = db.Column(db.Text)
    project = db.relationship("AdvisorProject")
    sale = db.relationship("Sale")


class AdvisorFortnightPayout(db.Model):
    """Libro de pagos de comisiones, independiente de Commission histórica por venta."""
    __tablename__ = "advisor_fortnight_payouts"
    id = db.Column(db.Integer, primary_key=True)
    advisor_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("advisor_projects.id"), nullable=False)
    period_start = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    paid_on = db.Column(db.Date, nullable=False, default=date.today)
    paid_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    advisor = db.relationship("Collaborator")
    project = db.relationship("AdvisorProject")
