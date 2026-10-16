"""Tarifario oficial de Impacto Media Agency (catálogo seguro e idempotente)."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models import ProductService, Renewal

HOSTING_FEE = Decimal("250.00")
HOSTING_TYPE = "Hosting y dominio"
OWNED_PLANS = {"Impacto Golden", "Impacto VIP"}
CUSTOM_PLAN_NAME = "Impacto Esencial Plus"
HOSTING_CREATION = {"Website - Creación", "Website + GBP - Creación"}
VIP_CHOICES = {
    "uniformes": "6 playeras + 6 gorras",
    "imprenta": "2,500 business cards + 2 magnetos vehiculares (18x24)",
}
GOOGLE = ["Google Business Profile", "Google Maps",
          "Gestión de incremento de reviews y calificaciones 5 estrellas"]


def item(name, price, months, category, sections, gifts=(), terms=(),
         modality="dueno", renewable=False, area="Desarrollo"):
    return dict(name=name, price=price, months=months, category=category,
                sections=sections, gifts=list(gifts), terms=list(terms),
                modality=modality, renewable=renewable, area=area)


PLANS = [
    item("Impacto Esencial - 6 Meses", "750", 6, "paquete", {
        "Google": GOOGLE,
        "Website": ["Dominio web .com", "Website responsive con 5 pestañas",
                    "Certificado SSL", "Click To Call", "Estimador virtual vinculado al correo"],
        "Redes sociales": ["Facebook", "Instagram", "Diseño de portada profesional",
                           "2 infografías comerciales"],
    }, ["Diseño de logotipo", "500 business cards (diseño, envío y taxes incluidos)",
        "Código QR enlazado a la website"],
    ["Duración de 6 meses; renovación por USD 750 cada semestre.",
     "Mantenimiento básico incluido: 2 cambios y 2 posteos de contenido al mes.",
     "Servicio administrado: el cliente no recibe accesos.", "Sin pagos mensuales."],
    "inquilino", True),
    item(CUSTOM_PLAN_NAME, "0", None, "paquete", {
        "Google": GOOGLE,
        "Website": ["Dominio web .com", "Website responsive con 5 pestañas",
                    "Certificado SSL", "Click To Call", "Estimador virtual vinculado al correo"],
        "Redes sociales": ["Facebook", "Instagram", "Diseño de portada profesional",
                           "2 infografías comerciales"],
    }, terms=["Beneficios base de Esencial; extras de Plus únicamente si se documentan.",
              "Precio y duración personalizados por contrato (meses).",
              "Mantenimiento según condiciones expresamente pactadas.",
              "Renovación por el importe y plazo acordados."],
    modality="inquilino", renewable=True),
    item("Impacto Golden", "2000", 12, "paquete", {
        "Google": GOOGLE,
        "Website": ["Hosting independiente", "Dominio .com",
                    "Website responsive con 5 pestañas", "SSL", "Click To Call",
                    "Estimador virtual vinculado al correo", "1 correo corporativo"],
        "Redes sociales": ["Facebook", "Instagram", "Diseño de portada profesional",
                           "3 infografías comerciales"],
    }, ["Diseño de logotipo (PNG/PDF)", "Código QR enlazado a la website",
        "1 video informativo-demostrativo",
        "1,000 business cards (diseño, envío y taxes incluidos)"],
    ["Entrega total de accesos y propiedad de plataformas al cliente.",
     "12 meses de mantenimiento básico gratis: 2 cambios y 2 posteos al mes.",
     "Al terminar los 12 meses, cesa el mantenimiento incluido; modificaciones extra se cobran aparte.",
     "No se renueva el pago inicial de USD 2,000.",
     "Anualidad de USD 250 por hosting y dominio desde el primer aniversario.",
     "Mantenimiento básico no significa gestión avanzada continua."]),
    item("Impacto VIP", "4000", None, "paquete", {
        "Google": GOOGLE + ["Emblema de Google", "Código QR enlazado a Google Business"],
        "Website": ["Hosting independiente", "Dominio web .com",
                    "Website responsive con 7 pestañas", "Pestaña de reviews",
                    "Pestaña de videos", "Landing pages por servicio", "SSL",
                    "Click To Call", "Estimador virtual", "Botón de WhatsApp",
                    "Diseño de logotipo", "3 correos corporativos",
                    "Código QR enlazado a website"],
        "Redes sociales": ["Facebook", "Instagram", "TikTok", "YouTube", "Pinterest",
                           "Vimeo", "X", "Diseño de portada",
                           "6 infografías comerciales", "3 reels de contenido"],
        "Directorios": ["LinkedIn", "Manta", "Houzz", "Porch",
                        "BuildZoom", "MerchantCircle"],
        "Material corporativo": ["Diseño de flyer", "Diseño de yard sign",
                                 "Diseño de hoja de estimados", "Diseño de hoja invoice"],
    }, ["Video con modelo IA",
        "Elegir solo un combo: 6 playeras + 6 gorras O 2,500 business cards + 2 magnetos (18x24)"],
    ["Plan de duración indefinida o de por vida; sin renovación de los USD 4,000.",
     "Propiedad y accesos completos entregados al cliente.",
     "Mantenimiento básico gratis indefinido: 2 posteos al mes, portada cada 3 meses y cambios ilimitados de información.",
     "No incluye rediseño de website, cambio de logo ni edición avanzada de fotos o videos.",
     "Anualidad de USD 250 por hosting y dominio desde el primer aniversario."]),
]

for label, price_all, price_social, reels, graphics, stories, ads in (
    ("Esencial", "300", "230", 2, 4, 0, 10),
    ("Silver", "400", "330", 4, 8, 36, 15),
    ("Golden", "500", "430", 8, 16, 90, 20),
):
    benefits = [f"{reels} reels diseñados y publicados cada mes",
                f"{graphics} infografías diseñadas y publicadas cada mes",
                "Soporte técnico y asistencia 24/7",
                ("Optimización básica de plataformas" if label == "Esencial"
                 else "Optimización avanzada de perfiles" if label == "Golden"
                 else "Actualización y optimización de perfiles"),
                "Respuesta a comentarios y reviews",
                "CTA y copies estratégicos orientados a conversión",
                "Diseño y cambio de portada mensual",
                "Calendario estratégico de publicaciones",
                ("Reporte mensual básico" if label == "Esencial"
                 else "Reporte mensual de rendimiento"),
                "Hasta 4 redes: Facebook, Instagram, TikTok y YouTube"]
    if stories:
        benefits += [f"{stories} historias al mes", "Cambios de información ilimitados"]
    else:
        benefits += ["3 posteos al mes con contenido del cliente",
                     "3 cambios de información mensuales"]
    for all_platforms, price in ((True, price_all), (False, price_social)):
        scope = "Website + GBP + Redes Sociales" if all_platforms else "Solo Redes Sociales"
        extra = ("Gestión de website y Google Business Profile" if all_platforms
                 else "No incluye gestión de website ni Google Business Profile")
        PLANS.append(item(
            f"Community Manager {label} - {scope}", price, 1, "community_manager",
            {"Gestión mensual": benefits + [extra]},
            [f"{ads} días de publicidad pagada de Meta Ads (FB e IG)"],
            ["Contrato mensual renovable.",
             "La cortesía son días de publicidad, no una asignación monetaria.",
             "No incluye inversión publicitaria en Google Ads, cambios de logo, rediseño web ni edición avanzada.",
             "El cliente mantiene control de sus credenciales y plataformas."],
            renewable=True, area="Social Media"))

for name, price, detail, annual in (
    ("Google Business Profile & Maps - Creación", "500", "Google Business Profile y Maps", False),
    ("Website - Creación", "700", "Website", True),
    ("Rediseño de Website", "500", "Rediseño de website", False),
    ("Website + GBP - Creación", "900", "Website + Google Business Profile", True),
):
    rules = ["Creación de pago único; mantenimiento mensual opcional y aparte."]
    if annual:
        rules.append("Hosting y dominio: USD 250 anuales.")
    PLANS.append(item(name, price, None, "servicio", {"Creación": [detail]}, terms=rules))

for label, price, scope in (
    ("GBP", "150", "Google Business Profile y Maps"),
    ("Website", "150", "Website"),
    ("Rediseño Web", "150", "Rediseño web"),
    ("Website + GBP", "250", "Website y Google Business Profile"),
):
    PLANS.append(item(
        f"Mantenimiento mensual {label}", price, 1, "mantenimiento",
        {"Mantenimiento": [f"Servicio mensual de {scope}",
            "Soporte técnico y asistencia 24/7", "Optimización de perfiles",
            "Posteo de contenido entregado por el cliente",
            "Cambios de información (sin cambios de logotipo ni rediseño web)",
            "Diseño de 2 reels al mes", "Diseño de 2 infografías al mes",
            "Gestión y respuesta de reviews", "Reporte mensual",
            "5% de descuento en servicios adicionales e imprenta"]},
        terms=["Servicio mensual opcional, contratado separadamente.",
               "No incluye edición avanzada de fotos o videos."],
        renewable=True))

BY_NAME = {x["name"]: x for x in PLANS}


def add_months(start, n):
    m_index = start.month - 1 + n
    year, month = start.year + m_index // 12, m_index % 12 + 1
    return date(year, month, min(start.day, monthrange(year, month)[1]))


def is_official(product):
    return bool(product and product.name in BY_NAME)


def get_plan(name):
    return BY_NAME.get(name or "")


def benefit_text(x):
    lines = []
    for section, features in x["sections"].items():
        lines.append(section.upper())
        lines.extend("- " + f for f in features)
        lines.append("")
    if x["gifts"]:
        lines.append("CORTESÍAS")
        lines.extend("- " + f for f in x["gifts"])
        lines.append("")
    lines.append("CONDICIONES COMERCIALES")
    lines.extend("- " + f for f in x["terms"])
    return "\n".join(lines).strip()


def seed_official():
    """Solo alta de productos faltantes: no altera contratos anteriores."""
    for x in PLANS:
        if ProductService.query.filter_by(name=x["name"]).first():
            continue
        db.session.add(ProductService(
            name=x["name"], category=x["category"],
            description="; ".join(x["terms"]), base_price=Decimal(x["price"]),
            currency="USD", duration_months=x["months"],
            modality=x["modality"],
            maintenance=("no_incluido" if x["category"] == "servicio" else "incluido"),
            responsible_area=x["area"], renewal_required=x["renewable"],
            active=True, is_physical=False, components=benefit_text(x)))


def is_principal_product(product):
    """Los paquetes de Impacto y Community Manager se contratan como principales."""
    return bool(product and product.category in {"paquete", "community_manager"})


def is_custom_plan(product):
    return bool(product and product.name == CUSTOM_PLAN_NAME)


def contract_months(contract):
    """Duración contractual efectiva, sin depender del catálogo mutable.

    En Plus se obtiene a partir del vencimiento real pactado. En contratos
    anteriores se da prioridad a la duración fija del catálogo.
    """
    if not contract or not contract.product:
        return None
    if is_custom_plan(contract.product):
        if not contract.starts_on or not contract.ends_on:
            return None
        for months in range(1, 61):
            if add_months(contract.starts_on, months) == contract.ends_on:
                return months
        return None
    return contract.product.duration_months or None


def contract_end(product, starts_on, duration_months=None):
    if product.name in OWNED_PLANS:
        return None
    months = duration_months if is_custom_plan(product) else product.duration_months
    if not months:
        return None
    return add_months(starts_on, int(months))


def maintenance_label(contract, today=None):
    product = contract.product
    if not product or product.category != "paquete" or product.name not in BY_NAME:
        return ""
    if product.name == "Impacto VIP":
        return "Mantenimiento básico gratuito de por vida."
    if product.name == "Impacto Golden":
        until = add_months(contract.starts_on, 12)
        state = "Finalizado" if (today or date.today()) > until else "Incluido"
        return f"{state}: mantenimiento básico hasta {until:%d/%m/%Y}."
    if is_custom_plan(product):
        if not contract.ends_on:
            return "Sin vencimiento registrado; verifica la duración acordada."
        until = contract.ends_on
        return f"Vigencia personalizada hasta {until:%d/%m/%Y}. El mantenimiento depende del acuerdo contratado."
    expected = contract_end(product, contract.starts_on)
    if expected and contract.ends_on != expected:
        return (
            f"Fechas inconsistentes: un plan de {product.duration_months} meses activado "
            f"el {contract.starts_on:%d/%m/%Y} debería vencer el {expected:%d/%m/%Y}. "
            "Revisa la activación y guarda la ficha para corregir el vencimiento."
        )
    until = contract.ends_on or expected
    if not until:
        return ""
    state = "Finalizado" if (today or date.today()) > until else "Incluido"
    return f"{state}: mantenimiento básico hasta {until:%d/%m/%Y}."


def cortesias(product, extra="", choice=""):
    x = get_plan(product.name)
    if not x:
        return (extra or "").strip()
    gifts = list(x["gifts"])
    if product.name == "Impacto VIP":
        if choice not in VIP_CHOICES:
            raise ValueError("Seleccione uno de los combos de imprenta incluidos en VIP.")
        gifts = ["Video con modelo IA", "Combo elegido: " + VIP_CHOICES[choice]]
    if (extra or "").strip():
        gifts.append("Acuerdo adicional: " + extra.strip())
    return "\n".join("- " + g for g in gifts)


def ensure_hosting_annuality(client_id, activation):
    """Alta única de vencimiento combinado hosting+dominio por USD 250."""
    existing = (Renewal.query.filter_by(
        client_id=client_id, contract_id=None, renewal_type=HOSTING_TYPE
    ).order_by(Renewal.id.desc()).first())
    if existing:
        return existing
    row = Renewal(client_id=client_id, contract_id=None, renewal_type=HOSTING_TYPE,
                  due_date=add_months(activation, 12), status="pendiente",
                  notes="USD 250/año hosting y dominio; no renueva el plan.")
    db.session.add(row)
    return row
