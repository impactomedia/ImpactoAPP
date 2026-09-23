from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.helpers import audit, next_code, save_upload
from app.models import Client, Sale, Collaborator, PrintOrder, PrintItem, DesignVersion, Shipment, ShipmentItem, PrintIncident
from app.services import update_print_order_status

bp = Blueprint("printing", __name__, url_prefix="/printing")


@bp.route("/")
@login_required
def index():
    orders = PrintOrder.query.order_by(PrintOrder.created_at.desc()).all()
    today = date.today()
    for order in orders:
        for shipment in order.shipments:
            if shipment.status == "en_transito" and shipment.estimated_delivery and shipment.estimated_delivery < today:
                order.status = "atrasado"
    db.session.commit()
    return render_template("printing/index.html", orders=orders)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_order():
    clients = Client.query.filter_by(record_type="cliente").order_by(Client.business_name).all()
    sales = Sale.query.order_by(Sale.id.desc()).all()
    collaborators = Collaborator.query.filter_by(status="activo").all()
    if request.method == "POST":
        order = PrintOrder(
            order_no=next_code("IMP", PrintOrder),
            client_id=request.form.get("client_id", type=int),
            sale_id=request.form.get("sale_id", type=int),
            responsible_id=request.form.get("responsible_id", type=int),
            provider=request.form.get("provider"),
            status="diseno",
            shipping_address=request.form.get("shipping_address"),
            total_cost=request.form.get("total_cost") or 0,
            total_sale=request.form.get("total_sale") or 0,
            notes=request.form.get("notes"),
        )
        db.session.add(order)
        db.session.flush()
        names = request.form.getlist("item_name[]")
        qtys = request.form.getlist("quantity[]")
        specs = request.form.getlist("specification[]")
        for i, name in enumerate(names):
            if name.strip():
                db.session.add(PrintItem(order_id=order.id, name=name.strip(), quantity=int(qtys[i] or 1), specification=specs[i] if i < len(specs) else None))
        audit("crear_orden_imprenta", "PrintOrder", order.id, after={"order_no": order.order_no})
        db.session.commit()
        flash("Orden de imprenta creada.", "success")
        return redirect(url_for("printing.detail", order_id=order.id))
    return render_template("printing/form.html", clients=clients, sales=sales, collaborators=collaborators)


@bp.route("/<int:order_id>")
@login_required
def detail(order_id):
    order = db.get_or_404(PrintOrder, order_id)
    return render_template("printing/detail.html", order=order)


@bp.route("/items/<int:item_id>/design", methods=["POST"])
@login_required
def upload_design(item_id):
    item = db.get_or_404(PrintItem, item_id)
    path = None
    if request.files.get("file"):
        try:
            path = save_upload(request.files.get("file"), prefix="design")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("printing.detail", order_id=item.order_id))
    version = (max([v.version for v in item.design_versions]) if item.design_versions else 0) + 1
    dv = DesignVersion(print_item_id=item.id, version=version, file_path=path, status="revision", comment=request.form.get("comment"), created_by_id=current_user.id)
    item.design_status = "revision"
    db.session.add(dv)
    audit("cargar_diseno", "PrintItem", item.id, after={"version": version})
    db.session.commit()
    flash("Versión de diseño cargada.", "success")
    return redirect(url_for("printing.detail", order_id=item.order_id))


@bp.route("/items/<int:item_id>/design-decision", methods=["POST"])
@login_required
def design_decision(item_id):
    item = db.get_or_404(PrintItem, item_id)
    decision = request.form.get("decision")
    if decision == "aprobado":
        item.design_status = "aprobado"
        item.design_approved_at = datetime.utcnow()
    elif decision == "cambios":
        item.design_status = "cambios"
        item.design_approved_at = None
    else:
        item.design_status = "rechazado"
        item.design_approved_at = None
    if item.design_versions:
        item.design_versions[-1].status = decision
        item.design_versions[-1].comment = request.form.get("comment") or item.design_versions[-1].comment
    update_print_order_status(item.order)
    audit("decision_diseno", "PrintItem", item.id, after={"decision": decision})
    db.session.commit()
    flash("Decisión de diseño registrada.", "success")
    return redirect(url_for("printing.detail", order_id=item.order_id))


@bp.route("/items/<int:item_id>/shipping-approval", methods=["POST"])
@login_required
def shipping_approval(item_id):
    item = db.get_or_404(PrintItem, item_id)
    if not item.design_approved_at:
        flash("Primero debe aprobarse el diseño.", "warning")
    else:
        item.shipping_approved_at = datetime.utcnow()
        update_print_order_status(item.order)
        audit("aprobar_envio", "PrintItem", item.id)
        db.session.commit()
        flash("Envío aprobado independientemente del diseño.", "success")
    return redirect(url_for("printing.detail", order_id=item.order_id))


@bp.route("/<int:order_id>/shipment", methods=["POST"])
@login_required
def create_shipment(order_id):
    order = db.get_or_404(PrintOrder, order_id)
    local = bool(request.form.get("local_delivery"))
    tracking = request.form.get("tracking_number")
    if not local and not tracking:
        flash("El tracking es obligatorio salvo entrega local.", "danger")
        return redirect(url_for("printing.detail", order_id=order.id))
    shipment = Shipment(
        order_id=order.id,
        carrier=request.form.get("carrier"),
        tracking_number=tracking,
        local_delivery=local,
        shipped_at=datetime.fromisoformat(request.form.get("shipped_at")) if request.form.get("shipped_at") else datetime.utcnow(),
        estimated_delivery=date.fromisoformat(request.form.get("estimated_delivery")) if request.form.get("estimated_delivery") else None,
        status="en_transito",
        notes=request.form.get("notes"),
    )
    if request.files.get("proof"):
        try:
            shipment.proof_path = save_upload(request.files.get("proof"), prefix="shipment")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("printing.detail", order_id=order.id))
    db.session.add(shipment)
    db.session.flush()
    for item in order.items:
        field = f"qty_{item.id}"
        qty = request.form.get(field, type=int) or 0
        if qty > 0:
            pending = item.quantity - item.qty_sent
            qty = min(qty, pending)
            db.session.add(ShipmentItem(shipment_id=shipment.id, print_item_id=item.id, quantity=qty))
            item.qty_sent += qty
    update_print_order_status(order)
    audit("producto_enviado", "PrintOrder", order.id, after={"tracking": tracking, "shipment_id": shipment.id})
    db.session.commit()
    flash("Envío registrado.", "success")
    return redirect(url_for("printing.detail", order_id=order.id))


@bp.route("/shipments/<int:shipment_id>/receive", methods=["POST"])
@login_required
def receive_shipment(shipment_id):
    shipment = db.get_or_404(Shipment, shipment_id)
    if not shipment.shipped_at:
        flash("No puede marcarse recibido sin fecha real de envío.", "danger")
        return redirect(url_for("printing.detail", order_id=shipment.order_id))
    total_received = 0
    total_sent = 0
    for si in shipment.shipment_items:
        qty = request.form.get(f"recv_{si.id}", type=int)
        if qty is None:
            qty = si.quantity
        qty = min(max(qty, 0), si.quantity)
        delta = qty - si.received_quantity
        si.received_quantity = qty
        si.print_item.qty_received += delta
        total_received += qty
        total_sent += si.quantity
    shipment.received_at = datetime.utcnow()
    shipment.status = "recibido" if total_received >= total_sent else "parcial"
    update_print_order_status(shipment.order)
    audit("producto_recibido", "Shipment", shipment.id, after={"status": shipment.status})
    db.session.commit()
    flash("Recepción registrada.", "success")
    return redirect(url_for("printing.detail", order_id=shipment.order_id))


@bp.route("/<int:order_id>/incident", methods=["POST"])
@login_required
def incident(order_id):
    order = db.get_or_404(PrintOrder, order_id)
    inc = PrintIncident(order_id=order.id, print_item_id=request.form.get("print_item_id", type=int), shipment_id=request.form.get("shipment_id", type=int), incident_type=request.form.get("incident_type", "no_recibido"), status="abierta", description=request.form.get("description", "Producto no recibido"))
    db.session.add(inc)
    update_print_order_status(order)
    audit("incidencia_imprenta", "PrintOrder", order.id, after={"incident": inc.incident_type})
    db.session.commit()
    flash("Incidencia abierta sin borrar el historial del envío.", "warning")
    return redirect(url_for("printing.detail", order_id=order.id))


@bp.route("/incidents/<int:incident_id>/resolve", methods=["POST"])
@login_required
def resolve_incident(incident_id):
    inc = db.get_or_404(PrintIncident, incident_id)
    inc.status = "resuelta"
    inc.resolution = request.form.get("resolution")
    update_print_order_status(inc.order)
    audit("resolver_incidencia_imprenta", "PrintIncident", inc.id, after={"resolution": inc.resolution})
    db.session.commit()
    flash("Incidencia resuelta.", "success")
    return redirect(url_for("printing.detail", order_id=inc.order_id))
