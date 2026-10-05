from scripts.reset_test_data import (
    CONFIRM_TOKEN,
    PARTIAL_TABLES,
    PRESERVED_TABLES,
    _operational_tables,
)


def test_reset_confirmation_token_is_explicit():
    assert CONFIRM_TOKEN == "BORRAR-DATOS-DE-PRUEBA"


def test_configuration_tables_are_preserved():
    assert {
        "roles", "permissions", "role_permissions", "advisor_projects",
        "work_schedules", "product_services", "commission_rules",
        "task_templates", "task_template_items", "catalog_items",
        "system_settings", "holidays",
    } <= PRESERVED_TABLES


def test_users_and_collaborators_are_partial_cleanup_tables():
    assert PARTIAL_TABLES == {"users", "collaborators"}


def test_operational_plan_includes_core_business_data():
    names = {table.name for table in _operational_tables()}
    for name in (
        "clients", "sales", "sale_items", "payments",
        "accounts_receivable", "projects", "tasks",
        "print_orders", "notifications", "audit_logs",
    ):
        assert name in names
    for name in (
        "roles", "permissions", "product_services",
        "system_settings", "users", "collaborators",
    ):
        assert name not in names
