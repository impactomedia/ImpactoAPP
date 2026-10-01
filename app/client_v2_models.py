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


class ClientInstallment(db.Model, TimestampMixin):
    __tablename__ = "client_installments"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, index=True)
    sequence = db.Column(db.Integer, nullable=False, default=1)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    paid_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    base_paid_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    due_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="pendiente", index=True)
    notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    client = db.relationship(
        "Client",
        backref=db.backref("installments", cascade="all, delete-orphan"),
    )
    sale = db.relationship(
        "Sale",
        backref=db.backref("installments", cascade="all, delete-orphan"),
    )
    created_by = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint("sale_id", "sequence", name="uq_client_installment_sale_sequence"),
    )


class ClientCollectionNote(db.Model, TimestampMixin):
    __tablename__ = "client_collection_notes"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), index=True)
    receivable_id = db.Column(db.Integer, db.ForeignKey("accounts_receivable.id"), index=True)
    note_type = db.Column(db.String(40), nullable=False, default="cobranza", index=True)
    promised_amount = db.Column(db.Numeric(12, 2))
    promise_date = db.Column(db.Date, index=True)
    status = db.Column(db.String(30), nullable=False, default="abierta", index=True)
    body = db.Column(db.Text, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    client = db.relationship(
        "Client",
        backref=db.backref("collection_notes", cascade="all, delete-orphan"),
    )
    sale = db.relationship(
        "Sale",
        backref=db.backref("collection_notes", cascade="all, delete-orphan"),
    )
    receivable = db.relationship("AccountReceivable", backref="collection_notes")
    created_by = db.relationship("User")


class ClientDocumentMeta(db.Model, TimestampMixin):
    __tablename__ = "client_document_meta"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    attachment_id = db.Column(db.Integer, db.ForeignKey("attachments.id"), nullable=False, unique=True, index=True)
    category = db.Column(db.String(50), nullable=False, default="otros", index=True)
    description = db.Column(db.String(255))

    client = db.relationship(
        "Client",
        backref=db.backref("document_metadata", cascade="all, delete-orphan"),
    )
    attachment = db.relationship(
        "Attachment",
        backref=db.backref(
            "client_document_meta",
            uselist=False,
            cascade="all, delete-orphan",
        ),
    )


class ClientTeamAssignment(db.Model, TimestampMixin):
    """Historial de responsables operativos por cliente, servicio y proyecto."""

    __tablename__ = "client_team_assignments"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    collaborator_id = db.Column(db.Integer, db.ForeignKey("collaborators.id"), nullable=False, index=True)
    contract_id = db.Column(db.Integer, db.ForeignKey("client_contracts.id"), index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), index=True)

    role_in_client = db.Column(db.String(120), nullable=False)
    service_label = db.Column(db.String(180))
    starts_on = db.Column(db.Date, nullable=False)
    ends_on = db.Column(db.Date)
    status = db.Column(db.String(30), nullable=False, default="activa", index=True)
    primary = db.Column(db.Boolean, default=False, nullable=False)
    notes = db.Column(db.Text)

    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    ended_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    end_reason = db.Column(db.String(255))
    legacy_assignment_id = db.Column(db.Integer, index=True)

    client = db.relationship(
        "Client",
        backref=db.backref(
            "team_assignments_v3",
            cascade="all, delete-orphan",
            order_by="ClientTeamAssignment.starts_on.desc()",
        ),
    )
    collaborator = db.relationship("Collaborator", foreign_keys=[collaborator_id], backref="client_team_assignments_v3")
    contract = db.relationship("ClientContract", backref="team_assignments_v3")
    project = db.relationship("Project", backref="team_assignments_v3")
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id])
    ended_by = db.relationship("User", foreign_keys=[ended_by_id])


class ClientImportBatch(db.Model, TimestampMixin):
    """Lote saneado de importación del Excel operativo.

    El archivo XLSX original no se almacena. payload_json conserva únicamente
    información procesada y excluye contraseñas/secretos detectados.
    """

    __tablename__ = "client_import_batches"

    id = db.Column(db.Integer, primary_key=True)
    file_name = db.Column(db.String(255), nullable=False)
    file_sha256 = db.Column(db.String(64), nullable=False, index=True)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(30), nullable=False, default="previewed", index=True)
    client_count = db.Column(db.Integer, nullable=False, default=0)
    payload_json = db.Column(db.JSON, nullable=False)
    result_json = db.Column(db.JSON)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    executed_at = db.Column(db.DateTime)

    created_by = db.relationship("User", backref="client_import_batches")
