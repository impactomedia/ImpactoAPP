"""Tarifas de medios de cobro y cargos reales vinculados a cada pago.

El cargo bancario es exclusivamente un gasto de la empresa:
no cambia Payment.amount, Sale.amount_paid, metas ni comisiones del asesor.
"""
from datetime import datetime

from app.extensions import db


class PaymentFeeRule(db.Model):
    __tablename__ = "payment_fee_rules"
    id = db.Column(db.Integer, primary_key=True)
    method_key = db.Column(db.String(40), nullable=False, unique=True, index=True)
    percentage = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    fixed_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaymentProcessingFee(db.Model):
    """Instantánea inmutable de la tarifa usada para un cobro confirmado."""
    __tablename__ = "payment_processing_fees"
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"), nullable=False, unique=True, index=True)
    method_key = db.Column(db.String(40), nullable=False)
    percentage = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    fixed_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    payment = db.relationship("Payment", backref=db.backref("processing_fee", uselist=False))
