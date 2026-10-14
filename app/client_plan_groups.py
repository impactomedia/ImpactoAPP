"""Consulta y clasificación de planes sin alterar contratos ni ventas."""
from sqlalchemy import and_, or_
from app.models import Client, ClientContract, PrintOrder, ProductService, Sale, SaleItem


PLAN_TYPES = {
    "paquete": "Planes principales",
    "community_manager": "Community Manager",
    "servicio": "Servicios individuales",
    "mantenimiento": "Mantenimiento",
    "imprenta": "Imprenta personalizada",
    "sin_plan": "Sin plan o servicio identificado",
}


def plan_condition(category):
    """Expresión EXISTS correlacionada para filtrar ANTES de paginar."""
    if category == "sin_plan":
        return ~or_(*(
            plan_condition(name) for name in PLAN_TYPES if name != "sin_plan"
        ))
    if category not in PLAN_TYPES:
        return None
    active = Client.contracts.any(and_(
        ClientContract.status == "activo",
        ClientContract.product.has(ProductService.category == category),
    ))
    if category == "paquete":
        return active
    bought = Client.sales.any(and_(
        Sale.status != "cancelada",
        Sale.items.any(SaleItem.product.has(ProductService.category == category)),
    ))
    if category == "imprenta":
        return or_(active, bought, Client.print_orders.any(PrintOrder.status != "cancelado"))
    return or_(active, bought)


def specific_plan_condition(product_id):
    """Un producto debe estar vigente o ser parte de una compra independiente."""
    active = Client.contracts.any(and_(
        ClientContract.status == "activo", ClientContract.product_id == product_id
    ))
    bought = Client.sales.any(and_(
        Sale.status != "cancelada",
        Sale.items.any(and_(
            SaleItem.product_id == product_id,
            SaleItem.product.has(ProductService.category.in_([
                "servicio", "mantenimiento", "community_manager", "imprenta",
            ])),
        )),
    ))
    return or_(active, bought)


def summaries_for_clients(clients):
    """Datos de presentación basados en relaciones existentes del cliente."""
    names_by_client = {}
    types_by_client = {}
    for client in clients:
        names, seen, categories = [], set(), set()
        def add_product(product):
            if not product or not product.name:
                return
            category = product.category if product.category in PLAN_TYPES else "servicio"
            categories.add(category)
            if product.name not in seen:
                names.append(product.name)
                seen.add(product.name)
        for contract in client.contracts:
            if contract.status == "activo":
                add_product(contract.product)
        for sale in client.sales:
            if sale.status != "cancelada":
                for item in sale.items:
                    if item.product and item.product.category in {
                        "imprenta", "servicio", "mantenimiento", "community_manager"
                    }:
                        add_product(item.product)
        for order in client.print_orders:
            if order.status != "cancelado":
                categories.add("imprenta")
                for item in order.items:
                    if item.name and item.name not in seen:
                        names.append(item.name)
                        seen.add(item.name)
        names_by_client[client.id] = names
        types_by_client[client.id] = categories or {"sin_plan"}
    return names_by_client, types_by_client
