"""Consulta y clasificación de planes sin alterar contratos ni ventas."""
from sqlalchemy import and_, or_
from app.models import Client, ClientContract, PrintOrder, ProductService, Sale, SaleItem


PLAN_TYPES = {
    "esencial": "Esencial",
    "golden": "Golden",
    "vip": "VIP",
    "premium": "Premium",
    "community_manager": "Community Manager",
    "imprenta": "Imprenta",
    "servicio_digital": "Servicio digital",
    "otros_planes": "Otros planes",
}


def _active_contract(product_filter):
    return Client.contracts.any(and_(
        ClientContract.status == "activo",
        ClientContract.product.has(product_filter),
    ))


def _valid_purchase(product_filter):
    return Client.sales.any(and_(
        Sale.status != "cancelada",
        Sale.items.any(SaleItem.product.has(product_filter)),
    ))


def plan_condition(category):
    """Clasifica por el nombre real del plan y sus servicios sin duplicar datos."""
    # Compatibilidad con enlaces y filtros históricos.
    if category == "paquete":
        return _active_contract(ProductService.category == "paquete")
    if category == "sin_plan":
        return ~or_(*(plan_condition(key) for key in PLAN_TYPES))
    if category in {"servicio", "mantenimiento"}:
        rule = ProductService.category == category
        return or_(_active_contract(rule), _valid_purchase(rule))

    if category in {"esencial", "golden", "vip", "premium", "otros_planes"}:
        keywords = ("esencial", "golden", "vip", "premium")
        if category == "otros_planes":
            rule = and_(
                ProductService.category == "paquete",
                *(~ProductService.name.ilike(f"%{word}%") for word in keywords),
            )
        else:
            rule = and_(
                ProductService.category == "paquete",
                ProductService.name.ilike(f"%{category}%"),
            )
        return _active_contract(rule)

    if category == "community_manager":
        rule = or_(
            ProductService.category == "community_manager",
            ProductService.name.ilike("%community manager%"),
        )
        return or_(_active_contract(rule), _valid_purchase(rule))

    if category == "imprenta":
        rule = ProductService.category == "imprenta"
        return or_(
            _active_contract(rule),
            _valid_purchase(rule),
            Client.print_orders.any(PrintOrder.status != "cancelado"),
        )

    if category == "servicio_digital":
        rule = ProductService.category.in_(["servicio", "mantenimiento"])
        return or_(_active_contract(rule), _valid_purchase(rule))

    return None


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
