from app.extensions import db
from app.models import TimestampMixin


class SaleOperationMeta(db.Model, TimestampMixin):
    """Metadata comercial de una venta en Impacto Nexora."""

    __tablename__ = "sale_operation_meta"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(
        db.Integer,
        db.ForeignKey("sales.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # principal_service | principal_upgrade | additional_purchase
    operation_type = db.Column(
        db.String(40),
        nullable=False,
        default="additional_purchase",
        index=True,
    )

    # contado | entrega | cuotas | financiamiento
    payment_mode = db.Column(
        db.String(40),
        nullable=False,
        default="contado",
        index=True,
    )
    payment_terms = db.Column(db.Text)

    # Para planes principales deja explícito el valor oficial y el crédito reconocido.
    catalog_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    credit_applied = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    sale = db.relationship(
        "Sale",
        backref=db.backref(
            "operation_meta",
            uselist=False,
            cascade="all, delete-orphan",
        ),
    )
    created_by = db.relationship("User")
