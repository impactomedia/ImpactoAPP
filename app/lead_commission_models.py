"""Atribución comercial por lead con comisiones independientes de los asesores."""
from datetime import date, datetime
from app.extensions import db


class LeadGenerator(db.Model):
    __tablename__ = "lead_generators"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(24), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    percentage = db.Column(db.Numeric(7, 3), nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True)


class ClientLeadOrigin(db.Model):
    __tablename__ = "client_lead_origins"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, unique=True)
    generator_id = db.Column(db.Integer, db.ForeignKey("lead_generators.id"), nullable=False)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    assigned_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    client = db.relationship("Client", backref=db.backref("lead_origin", uselist=False))
    generator = db.relationship("LeadGenerator")


class SaleLeadAttribution(db.Model):
    """Copia fija de generador y porcentaje al primer cobro de la venta."""
    __tablename__ = "sale_lead_attributions"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, unique=True)
    generator_id = db.Column(db.Integer, db.ForeignKey("lead_generators.id"), nullable=False)
    percentage = db.Column(db.Numeric(7, 3), nullable=False)
    assigned_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sale = db.relationship("Sale", backref=db.backref("lead_attribution", uselist=False))
    generator = db.relationship("LeadGenerator")


class LeadCommissionPayout(db.Model):
    """Registro de desembolsos; generar comisión no implica pagarla."""
    __tablename__ = "lead_commission_payouts"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    generator_id = db.Column(db.Integer, db.ForeignKey("lead_generators.id"), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey("advisor_projects.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    paid_on = db.Column(db.Date, nullable=False, default=date.today)
    paid_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sale = db.relationship("Sale")
    generator = db.relationship("LeadGenerator")
    project = db.relationship("AdvisorProject")
