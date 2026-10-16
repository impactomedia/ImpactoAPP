"""Pruebas de límites empresariales y cumplimiento de planes contratados."""
from datetime import date
from pathlib import Path

import pytest

from app import create_app
from app.client_v2_models import ClientContractDetail
from app.extensions import db
from app.models import (
    AdvisorProject, Client, ClientComment, ClientContract, Collaborator,
    ProductService, Project, Role, Task, User,
)
from app.plan_benefit_tasks import contracted_benefits, sync_project_benefits
from scripts.seed import seed_all


class Config:
    TESTING = True
    PROJECT_ADMIN_SCOPE_ENABLED = True
    SECRET_KEY = "test-project-admin-development"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    UPLOAD_FOLDER = str(Path(__file__).parent / "uploads_project_tenant")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    COMPANY_NAME = "Impacto Media Agency"
    COMPANY_TIMEZONE = "America/Managua"
    SMTP_HOST = None
    SMTP_PORT = 587
    SMTP_USER = None
    SMTP_PASSWORD = None
    SMTP_FROM = "test@example.com"
    SMTP_USE_TLS = False


@pytest.fixture
def prepared():
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        seed_all()
        projects = {p.code: p for p in AdvisorProject.query.all()}
        roles = {r.name: r for r in Role.query.all()}

        accounts = {}
        for username, role_name, tenant in [
            ("Novax Admin", "admin", "NOVAX"),
            ("Impacto Admin", "admin", "IMPACTO"),
            ("Novax Asesor", "advisor", "NOVAX"),
            ("Impacto Asesor", "advisor", "IMPACTO"),
            ("Desarrollador Ana", "developer", None),
        ]:
            email = username.lower().replace(" ", "-") + "@test.local"
            user = User(name=username, email=email, role=roles[role_name], active=True)
            user.set_password("TestingPassword987")
            db.session.add(user)
            db.session.flush()
            collaborator = Collaborator(
                user_id=user.id,
                code="COL-" + str(user.id),
                department="Desarrollo" if role_name == "developer" else "Comercial",
                job_title=role_name,
                status="activo",
                advisor_project_id=projects[tenant].id if tenant else None,
            )
            db.session.add(collaborator)
            db.session.flush()
            accounts[username] = (user.id, collaborator.id, email)

        novax = Client(
            code="NOVAX-1", business_name="Cliente Novax",
            contact_name="Cliente Novax Contacto", record_type="cliente",
            client_status="activo", owner_id=accounts["Novax Asesor"][1],
        )
        impacto = Client(
            code="IMPACTO-1", business_name="Cliente Impacto",
            contact_name="Cliente Impacto Contacto", record_type="cliente",
            client_status="activo", owner_id=accounts["Impacto Asesor"][1],
        )
        db.session.add_all([novax, impacto])
        db.session.flush()
        db.session.add(ClientComment(
            client_id=novax.id, user_id=accounts["Novax Asesor"][0],
            process_type="Cotización / Venta", body="Nota interna comercial de Novax",
        ))
        product = ProductService(
            name="Plan beneficio prueba", category="paquete",
            components=("WEBSITE\n- Website responsive\n- Certificado SSL\n\n"
                        "GOOGLE\n- Google Business Profile\n\n"
                        "CORTESÍAS\n- Cortesía no acordada\n\n"
                        "CONDICIONES COMERCIALES\n- No incluye rediseño"),
            responsible_area="Desarrollo", active=True,
        )
        db.session.add(product)
        db.session.flush()
        contract = ClientContract(
            client_id=novax.id, product_id=product.id,
            starts_on=date.today(), status="activo",
        )
        db.session.add(contract)
        db.session.flush()
        db.session.add(ClientContractDetail(
            contract_id=contract.id,
            benefits_snapshot=product.components,
            courtesies_snapshot="Código QR acordado",
        ))
        delivery = Project(
            project_no="PRJ-NOVAX-01", client_id=novax.id,
            product_id=product.id, name="Plan Novax",
            department="Desarrollo", progress=0,
        )
        db.session.add(delivery)
        db.session.flush()

        own = Task(
            title="Asignación Ana", client_id=novax.id, project_id=delivery.id,
            assignee_id=accounts["Desarrollador Ana"][1],
            status="pendiente",
        )
        db.session.add(own)
        db.session.commit()

        yield app, accounts, novax.id, impacto.id, delivery.id, contract.id
        db.session.remove()


def login(browser, address):
    return browser.post("/auth/login", data={
        "email": address, "password": "TestingPassword987"
    })


def test_one_task_for_each_actual_benefit_without_overwriting_progress(prepared):
    app, accounts, novax_id, impacto_id, project_id, contract_id = prepared
    with app.app_context():
        project = db.session.get(Project, project_id)
        contract = db.session.get(ClientContract, contract_id)
        count = sync_project_benefits(project, contract)
        assert count == 4
        benefits = Task.query.filter_by(project_id=project_id, task_type="beneficio_plan").all()
        assert len(benefits) == 4
        assert {x.title for x in benefits} == {
            "Website responsive", "Certificado SSL", "Google Business Profile",
            "Código QR acordado",
        }
        benefits[0].status = "completada"
        db.session.commit()
        assert sync_project_benefits(project, contract) == 0
        assert Task.query.filter_by(id=benefits[0].id).one().status == "completada"


def test_developer_sees_every_internal_comment_and_can_close_benefits(prepared):
    app, accounts, novax_id, impacto_id, project_id, contract_id = prepared
    with app.app_context():
        sync_project_benefits(db.session.get(Project, project_id))
        db.session.commit()
        task = Task.query.filter_by(project_id=project_id, task_type="beneficio_plan").first()
        task_id = task.id

    browser = app.test_client()
    login(browser, accounts["Desarrollador Ana"][2])
    response = browser.get(f"/clients/{novax_id}/coordination")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Nota interna comercial de Novax" in page
    assert "Línea de tiempo" in page
    assert "Beneficios / tareas" in page
    assert "Google Business Profile" in page

    other = browser.get(f"/clients/{impacto_id}/coordination")
    assert other.status_code in (403, 404)
    new_note = browser.post(f"/clients/{novax_id}/coordination/comment", data={
        "process_type": "Desarrollo web", "body": "Cambios de Ana",
    })
    assert new_note.status_code == 302
    updated = browser.post(f"/operations/tasks/{task_id}/update", data={
        "status": "completada",
    })
    assert updated.status_code == 302
    with app.app_context():
        assert Task.query.get(task_id).status == "completada"
        assert ClientComment.query.filter_by(body="Cambios de Ana").one().user_id == accounts["Desarrollador Ana"][0]


def test_novax_admin_cannot_access_impacto_records_or_global_modules(prepared):
    app, accounts, novax_id, impacto_id, project_id, contract_id = prepared
    browser = app.test_client()
    login(browser, accounts["Novax Admin"][2])

    landing = browser.get("/dashboard/")
    assert landing.status_code == 200
    html = landing.get_data(as_text=True)
    assert "Proyecto NOVAX" in html
    assert "Cliente Impacto" not in html

    own = browser.get(f"/clients/{novax_id}")
    assert own.status_code == 200
    other = browser.get(f"/clients/{impacto_id}")
    assert other.status_code == 403
    assert browser.get(f"/clients/{impacto_id}/coordination").status_code == 403
    assert browser.get("/hr/collaborators").status_code == 403
    assert browser.get("/finance/").status_code == 403
    assert browser.get("/dashboard/search?q=Impacto").status_code == 403
    assert browser.post(f"/clients/{novax_id}/comment", data={
        "body": "Un nuevo comentario", "process_type": "Seguimiento / CRM",
        "owner_id": accounts["Impacto Asesor"][1],
    }).status_code == 403



def test_project_admin_can_only_renew_services_in_own_company(prepared):
    from datetime import timedelta
    from app.models import Renewal
    app, accounts, novax_id, impacto_id, *_ = prepared
    with app.app_context():
        own = Renewal(
            client_id=novax_id, renewal_type="Dominio",
            due_date=date.today() + timedelta(days=12), status="pendiente",
        )
        other = Renewal(
            client_id=impacto_id, renewal_type="Dominio",
            due_date=date.today() + timedelta(days=12), status="pendiente",
        )
        db.session.add_all([own, other])
        db.session.commit()
        own_id, other_id = own.id, other.id

    browser = app.test_client()
    login(browser, accounts["Novax Admin"][2])
    assert browser.get("/renewals/").status_code == 200
    html = browser.get("/renewals/").get_data(as_text=True)
    assert "Cliente Novax" in html
    assert "Cliente Impacto" not in html
    assert browser.get(f"/renewals/{own_id}/renew").status_code == 200
    assert browser.get(f"/renewals/{other_id}/renew").status_code == 403
    assert browser.post(f"/renewals/{other_id}/complete", data={
        "payment_mode": "contado", "payment_date": date.today().isoformat(),
        "amount": "120", "payment_method": "Zelle",
    }).status_code == 403



def test_edit_sales_obeys_business_project_and_advisor_cartridge(prepared):
    from app.models import Sale, SaleItem, AuditLog
    app, accounts, novax_id, impacto_id, *_ = prepared
    with app.app_context():
        rows = []
        for idx, (customer_id, advisor) in enumerate((
            (novax_id, accounts["Novax Asesor"][1]),
            (impacto_id, accounts["Impacto Asesor"][1]),
        ), 1):
            sale = Sale(sale_no=f"SALE-PROJECT-{idx}",client_id=customer_id,
                        advisor_id=advisor,sale_date=date.today(),
                        status="confirmada",currency="USD",total=40,amount_paid=0,balance=40)
            db.session.add(sale)
            db.session.flush()
            item=SaleItem(sale_id=sale.id,description="Accesorio comercial",
                          quantity=1,list_price=40,discount=0,unit_price=40,total=40)
            db.session.add(item)
            rows.append((sale.id,item))
        db.session.commit()
        novax_sale, impacto_sale = rows[0][0],rows[1][0]
        impacto_item = rows[1][1].id

    browser=app.test_client()
    login(browser,accounts["Novax Admin"][2])
    assert browser.get(f"/sales/{novax_sale}/edit").status_code == 200
    assert browser.get(f"/sales/{impacto_sale}/edit").status_code in (403,404)
    denied=browser.post(f"/sales/{impacto_sale}/edit",data={
        "sale_date":"2026-03-20",
        f"item_description_{impacto_item}":"Accesorio editado indebidamente",
        "reason":"Intento de cambio fuera del proyecto",
    })
    assert denied.status_code in (403,404)
    with app.app_context():
        assert db.session.get(Sale, impacto_sale).sale_date==date.today()
        assert AuditLog.query.filter_by(action="editar_venta",entity_id=str(impacto_sale)).count()==0

    browser.post("/auth/logout")
    login(browser,accounts["Novax Asesor"][2])
    assert browser.get(f"/sales/{novax_sale}/edit").status_code == 200
    assert browser.get(f"/sales/{impacto_sale}/edit").status_code in (403,404)
