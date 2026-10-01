from app.extensions import db
from app.models import TimestampMixin


class ClientOperationalProfile(db.Model, TimestampMixin):
    __tablename__ = "client_operational_profiles"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, unique=True, index=True)

    attention_days = db.Column(db.String(160))
    business_hours = db.Column(db.String(255))
    experience_text = db.Column(db.String(120))
    coverage_text = db.Column(db.String(180))
    payment_methods = db.Column(db.Text)
    estimate_policy = db.Column(db.String(255))
    languages = db.Column(db.String(160))

    operational_email = db.Column(db.String(190))
    corporate_email = db.Column(db.String(190))
    services_to_promote = db.Column(db.Text)
    logo_status = db.Column(db.Text)
    brand_colors = db.Column(db.String(255))

    domain_activated_on = db.Column(db.Date)
    domain_renews_on = db.Column(db.Date)
    hosting_activated_on = db.Column(db.Date)
    hosting_renews_on = db.Column(db.Date)
    domain_notes = db.Column(db.Text)
    operational_notes = db.Column(db.Text)

    client = db.relationship(
        "Client",
        backref=db.backref(
            "operational_profile",
            uselist=False,
            cascade="all, delete-orphan",
        ),
    )


class ClientPlatform(db.Model, TimestampMixin):
    __tablename__ = "client_platforms"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    platform_key = db.Column(db.String(60), nullable=False)
    label = db.Column(db.String(120), nullable=False)
    url = db.Column(db.String(500))
    status = db.Column(db.String(30), default="activo", nullable=False, index=True)
    notes = db.Column(db.Text)

    client = db.relationship(
        "Client",
        backref=db.backref(
            "platform_links",
            cascade="all, delete-orphan",
        ),
    )

    __table_args__ = (
        db.UniqueConstraint("client_id", "platform_key", name="uq_client_platform"),
    )


class ClientContractDetail(db.Model, TimestampMixin):
    __tablename__ = "client_contract_details"

    id = db.Column(db.Integer, primary_key=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("client_contracts.id"), nullable=False, unique=True, index=True)
    modality_snapshot = db.Column(db.String(40))
    maintenance_snapshot = db.Column(db.String(80))
    benefits_snapshot = db.Column(db.Text)
    courtesies_snapshot = db.Column(db.Text)
    status_reason = db.Column(db.Text)

    contract = db.relationship(
        "ClientContract",
        backref=db.backref(
            "v2_detail",
            uselist=False,
            cascade="all, delete-orphan",
        ),
    )
