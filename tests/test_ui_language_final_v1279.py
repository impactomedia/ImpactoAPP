"""Comprobación de nombres y mensajes visibles al usuario de Nexora."""
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"

REVIEWED = [
    "app_base.html",
    "clients/detail.html",
    "clients/form.html",
    "clients/coordination.html",
    "clients/import_workbook.html",
    "clients/mine.html",
    "sales/detail.html",
    "sales/form.html",
    "sales/index.html",
    "sales/principal_form.html",
    "settings/backups.html",
    "finance_control/goals.html",
]


@pytest.mark.parametrize("relative_path", REVIEWED)
def test_major_screens_do_not_show_old_application_names(relative_path):
    content = (TEMPLATES / relative_path).read_text(encoding="utf-8")
    assert "Impacto Manager" not in content
    assert "Impacto Nexora" not in content
    assert "Clientes V2" not in content
    assert "v1.19.1" not in content


def test_crm_sidebar_matches_screens():
    sidebar = (TEMPLATES / "app_base.html").read_text(encoding="utf-8")
    pipeline = (TEMPLATES / "crm/pipeline.html").read_text(encoding="utf-8")
    assert '<i class="bi bi-funnel"></i> Seguimientos' in sidebar
    assert '<i class="bi bi-kanban"></i> Embudo de ventas' in sidebar
    assert '<h1>Embudo de ventas</h1>' in pipeline


def test_finance_texts_match_automatic_commission_policy():
    goals = (TEMPLATES / "finance_control/goals.html").read_text(encoding="utf-8")
    advisor = (TEMPLATES / "finance_control/advisor.html").read_text(encoding="utf-8")
    assert "Meta Neta que habilita las comisiones" not in goals
    assert "Meta Neta para comisionar" not in advisor
    assert "comisiones se calculan automáticamente" in goals


def test_original_business_rules_are_not_removed_from_lead_attribution():
    detail = (TEMPLATES / "sales/detail.html").read_text(encoding="utf-8")
    assert 'name="lead_generator_id"' in detail
    assert 'name="reason"' in detail
    assert "lead_has_payout" in detail
