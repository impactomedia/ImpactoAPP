from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from app.extensions import db
from app.helpers import audit, next_code, save_upload
from app.models import (
    Client,
    Collaborator,
    DesignVersion,
    PrintIncident,
    PrintItem,
    PrintOrder,
    Sale,
    Shipment,
    ShipmentItem,
)
from app.services import update_print_order_status

bp = Blueprint("printing", __name__, url_prefix="/printing")

INCIDENT_TYPES = {"no_recibido", "danado", "faltante", "reimpresion", "reenvio"}
DESIGN_DECISIONS = {"aprobado", "cambios", "rechazado"}


def _role_name():
    return current_user.role.name if current_user.role else ""


def _team_ids():
    collaborator = current_user.collaborator
    if not collaborator:
        return []
    return [collaborator.id] + [row.id for row in collaborator.subordinates]


def _visible_clients_query():
    query = Client.query.filter(
        Client.record_type == "cliente",
        Client.client_status != "archivado",
    )
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(Client.owner_id == collaborator.id)
    elif role == "supervisor" and collaborator:
        query = query.filter(Client.owner_id.in_(_team_ids()))
    return query


def _visible_sales_query():
    query = Sale.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(
            or_(
                Sale.advisor_id == collaborator.id,
                Sale.client.has(Client.owner_id == collaborator.id),
            )
        )
    elif role == "supervisor" and collaborator:
        ids = _team_ids()
        query = query.filter(
            or_(
                Sale.advisor_id.in_(ids),
                Sale.client.has(Client.owner_id.in_(ids)),
            )
        )
    return query


def _visible_orders_query():
    query = PrintOrder.query
    role = _role_name()
    collaborator = current_user.collaborator
    if role == "advisor" and collaborator:
        query = query.filter(PrintOrder.client.has(Client.owner_id == collaborator.id))
    elif role == "supervisor" and collaborator:
        query = query.filter(PrintOrder.client.has(Client.owner_id.in_(_team_ids())))
    return query


@bp.route("/")
@login_required
def index():
    orders = _visible_orders_query().order_by(PrintOrder.created_at.desc()).all()
    today = date.today()
    changed = False
    for order in orders:
        if order.status in {"recibido", "cancelado"}:
            continue
        for shipment in order.shipments:
            if (
                shipment.status in {"en_transito", "parcial"}
                and shipment.estimated_delivery
                and shipment.estimated_delivery < today
                and order.status != "atrasado"
            ):
                order.status = "atrasado"
                changed = True
                break
    if changed:
        db.session.commit()
    return render_template("printing/index.html", orders=orders)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_order():
    clients = _visible_clients_query().order_by(Client.business_name).all()
    sales = _visible_sales_query().order_by(Sale.id.desc()).all()
    collaborators = Collaborator.query.filter_by(status="activo").order_by(Collaborator.id).all()

    client_ids = {client.id for client in clients}
    sale_ids = {sale.id for sale in sales}

    if request.method == "POST":
        client_id = request.form.get("client_id", type=int)
        sale_id = request.form.get("sale_id", type=int)
        if not client_id or client_id not in client_ids:
            abort(403)
        if sale_id and sale_id not in sale_ids:
            abort(403)

        if sale_id:
            sale = db.session.get(Sale, sale_id)
            if not sale or sale.client_id != client_id:
                flash("La venta seleccionada no pertenece al cliente indicado.", "danger")
                return render_template("printing/form.html", clients=clients, sales=sales, collaborators=collaborators)

        order = PrintOrder(
            order_no=next_code("IMP", PrintOrder),
            client_id=client_id,
            sale_id=sale_id,
            responsible_id=request.form.get("responsible_id", type=int),
            provider=(request.form.get("provider") or "").strip() or None,
            status="diseno",
            shipping_address=(request.form.get("shipping_address") or "").strip() or None,
            total_cost=request.form.get("total_cost") or 0,
            total_sale=request.form.get("total_sale") or 0,
            notes=request.form.get("notes"),
        )
        db.session.add(order)
        db.session.flush()

        names = request.form.getlist("item_name[]")
        quantities = request.form.getlist("quantity[]")
        specifications = request.form.getlist("specification[]")
        created_items = 0

        for index, raw_name in enumerate(names):
            name = (raw_name or "").strip()
            if not name:
                continue
            try:
                quantity = int(quantities[index] or 1)
            except (ValueError, IndexError):
                quantity = 0
            if quantity <= 0:
                db.session.rollback()
                flash("La cantidad de cada producto debe ser mayor que cero.", "danger")
                return render_template("printing/form.html", clients=clients, sales=sales, collaborators=collaborators)

            db.session.add(
                PrintItem(
                    order_id=order.id,
                    name=name,
                    quantity=quantity,
                    specification=specifications[index] if index < len(specifications) else None,
                )
            )
            created_items += 1

        if not created_items:
            db.session.rollback()
            flash("Agrega al menos un producto a la orden de imprenta.", "danger")
            return render_template("printing/form.html", clients=clients, sales=sales, collaborators=collaborators)

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
    file_storage = request.files.get("file")
    if not file_storage or not file_storage.filename:
        flash("Selecciona un archivo de diseño.", "warning")
        return redirect(url_for("printing.detail", order_id=item.order_id))

    try:
        path = save_upload(file_storage, prefix="design")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("printing.detail", order_id=item.order_id))

    version = (max([row.version for row in item.design_versions]) if item.design_versions else 0) + 1
    design = DesignVersion(
        print_item_id=item.id,
        version=version,
        file_path=path,
        status="revision",
        comment=request.form.get("comment"),
        created_by_id=current_user.id,
    )
    item.design_status = "revision"
    item.design_approved_at = None
    item.shipping_approved_at = None
    db.session.add(design)
    update_print_order_status(item.order)
    audit("cargar_diseno", "PrintItem", item.id, after={"version": version})
    db.session.commit()
    flash("Versión de diseño cargada.", "success")
    return redirect(url_for("printing.detail", order_id=item.order_id))


@bp.route("/items/<int:item_id>/design-decision", methods=["POST"])
@login_required
def design_decision(item_id):
    item = db.get_or_404(PrintItem, item_id)
    decision = request.form.get("decision")
    if decision not in DESIGN_DECISIONS:
        flash("Decisión de diseño no válida.", "danger")
        return redirect(url_for("printing.detail", order_id=item.order_id))

    if decision == "aprobado":
        item.design_status = "aprobado"
        item.design_approved_at = datetime.utcnow()
    elif decision == "cambios":
        item.design_status = "cambios"
        item.design_approved_at = None
        item.shipping_approved_at = None
    else:
        item.design_status = "rechazado"
        item.design_approved_at = None
        item.shipping_approved_at = None

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
    elif item.shipping_approved_at:
        flash("Este producto ya tiene el envío aprobado.", "info")
    else:
        item.shipping_approved_at = datetime.utcnow()
        update_print_order_status(item.order)
        audit("aprobar_envio", "PrintItem", item.id)
        db.session.commit()
        flash("Envío aprobado.", "success")
    return redirect(url_for("printing.detail", order_id=item.order_id))


@bp.route("/<int:order_id>/shipment", methods=["POST"])
@login_required
def create_shipment(order_id):
    order = db.get_or_404(PrintOrder, order_id)
    local_delivery = bool(request.form.get("local_delivery"))
    tracking = (request.form.get("tracking_number") or "").strip() or None

    if not local_delivery and not tracking:
        flash("El tracking es obligatorio salvo que sea una entrega local.", "danger")
        return redirect(url_for("printing.detail", order_id=order.id))

    quantities = []
    for item in order.items:
        requested = request.form.get(f"qty_{item.id}", type=int) or 0
        pending = max(0, item.quantity - item.qty_sent)
        requested = min(max(requested, 0), pending)
        if requested > 0:
            if not item.shipping_approved_at:
                flash(f"Debes aprobar el envío de {item.name} antes de despacharlo.", "danger")
                return redirect(url_for("printing.detail", order_id=order.id))
            quantities.append((item, requested))

    if not quantities:
        flash("Indica al menos una cantidad para despachar.", "warning")
        return redirect(url_for("printing.detail", order_id=order.id))

    shipped_at_raw = request.form.get("shipped_at")
    try:
        shipped_at = datetime.fromisoformat(shipped_at_raw) if shipped_at_raw else datetime.utcnow()
    except ValueError:
        shipped_at = datetime.utcnow()

    estimated_raw = request.form.get("estimated_delivery")
    try:
        estimated_delivery = date.fromisoformat(estimated_raw) if estimated_raw else None
    except ValueError:
        estimated_delivery = None

    shipment = Shipment(
        order_id=order.id,
        carrier=(request.form.get("carrier") or "").strip() or None,
        tracking_number=tracking,
        local_delivery=local_delivery,
        shipped_at=shipped_at,
        estimated_delivery=estimated_delivery,
        status="en_transito",
        notes=request.form.get("notes"),
    )

    if request.files.get("proof") and request.files.get("proof").filename:
        try:
            shipment.proof_path = save_upload(request.files.get("proof"), prefix="shipment")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("printing.detail", order_id=order.id))

    db.session.add(shipment)
    db.session.flush()
    for item, quantity in quantities:
        db.session.add(ShipmentItem(shipment_id=shipment.id, print_item_id=item.id, quantity=quantity))
        item.qty_sent += quantity

    update_print_order_status(order)
    audit("producto_enviado", "PrintOrder", order.id, after={"tracking": tracking, "shipment_id": shipment.id})
    db.session.commit()
    flash("Envío registrado.", "success")
    return redirect(url_for("printing.detail", order_id=order.id))


@bp.route("/shipments/<int:shipment_id>/receive", methods=["POST"])
@login_required
def receive_shipment(shipment_id):
    shipment = db.get_or_404(Shipment, shipment_id)
    if shipment.status == "recibido":
        flash("Este envío ya fue recibido completamente.", "info")
        return redirect(url_for("printing.detail", order_id=shipment.order_id))
    if not shipment.shipped_at:
        flash("No puede marcarse recibido sin fecha real de envío.", "danger")
        return redirect(url_for("printing.detail", order_id=shipment.order_id))
    if not shipment.shipment_items:
        flash("El envío no tiene productos asociados.", "danger")
        return redirect(url_for("printing.detail", order_id=shipment.order_id))

    total_received = 0
    total_sent = 0
    for shipment_item in shipment.shipment_items:
        quantity = request.form.get(f"recv_{shipment_item.id}", type=int)
        if quantity is None:
            quantity = shipment_item.received_quantity
        quantity = min(max(quantity, 0), shipment_item.quantity)
        delta = quantity - shipment_item.received_quantity
        shipment_item.received_quantity = quantity
        shipment_item.print_item.qty_received = max(0, shipment_item.print_item.qty_received + delta)
        total_received += quantity
        total_sent += shipment_item.quantity

    shipment.status = "recibido" if total_received >= total_sent else "parcial"
    shipment.received_at = datetime.utcnow() if shipment.status == "recibido" else None
    update_print_order_status(shipment.order)
    audit("producto_recibido", "Shipment", shipment.id, after={"status": shipment.status})
    db.session.commit()
    flash("Recepción actualizada.", "success")
    return redirect(url_for("printing.detail", order_id=shipment.order_id))


@bp.route("/<int:order_id>/incident", methods=["POST"])
@login_required
def incident(order_id):
    order = db.get_or_404(PrintOrder, order_id)
    incident_type = request.form.get("incident_type", "no_recibido")
    description = (request.form.get("description") or "").strip()
    if incident_type not in INCIDENT_TYPES:
        flash("Tipo de incidencia no válido.", "danger")
        return redirect(url_for("printing.detail", order_id=order.id))
    if not description:
        flash("Describe la incidencia antes de guardarla.", "warning")
        return redirect(url_for("printing.detail", order_id=order.id))

    row = PrintIncident(
        order_id=order.id,
        print_item_id=request.form.get("print_item_id", type=int),
        shipment_id=request.form.get("shipment_id", type=int),
        incident_type=incident_type,
        status="abierta",
        description=description,
    )
    db.session.add(row)
    db.session.flush()
    update_print_order_status(order)
    audit("incidencia_imprenta", "PrintOrder", order.id, after={"incident": row.incident_type})
    db.session.commit()
    flash("Incidencia abierta sin borrar el historial del envío.", "warning")
    return redirect(url_for("printing.detail", order_id=order.id))


@bp.route("/incidents/<int:incident_id>/resolve", methods=["POST"])
@login_required
def resolve_incident(incident_id):
    incident_row = db.get_or_404(PrintIncident, incident_id)
    if incident_row.status != "abierta":
        flash("La incidencia ya está resuelta.", "info")
        return redirect(url_for("printing.detail", order_id=incident_row.order_id))

    resolution = (request.form.get("resolution") or "").strip()
    if not resolution:
        flash("Indica la solución aplicada.", "warning")
        return redirect(url_for("printing.detail", order_id=incident_row.order_id))

    incident_row.status = "resuelta"
    incident_row.resolution = resolution
    update_print_order_status(incident_row.order)
    audit("resolver_incidencia_imprenta", "PrintIncident", incident_row.id, after={"resolution": resolution})
    db.session.commit()
    flash("Incidencia resuelta.", "success")
    return redirect(url_for("printing.detail", order_id=incident_row.order_id))
