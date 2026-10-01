"""
Синхронизация кассовой смены CRM (construction.CashShift) с облаком eKassa.

Одна EkassaIntegration на компанию = один fiscal_number. В eKassa одна открытая смена на ККМ:
- shift_open — когда в CRM у компании ровно одна смена в статусе OPEN (первая открытая);
- shift_close — когда после закрытия в CRM у компании не осталось ни одной OPEN-смены.

Ошибки eKassa не откатывают локальную смену (только лог). Чеки продаж — try_fiscalize_pos_sale в Sale.mark_paid.
"""
from __future__ import annotations

import logging

from apps.ekassa.client import EkassaHttpClient
from apps.ekassa.exceptions import EkassaAPIError, EkassaConfigurationError
from apps.ekassa.services import get_integration

logger = logging.getLogger(__name__)


def _shift_body(fiscal_number: str) -> dict:
    return {"fiscal_number": (fiscal_number or "").strip(), "html": "false", "css": "false"}


def sync_ekassa_after_local_shift_open_by_id(shift_id) -> None:
    """Вызов из фона по id смены (свежее состояние из БД)."""
    from apps.construction.models import CashShift

    sh = CashShift.objects.filter(pk=shift_id).select_related("company").first()
    if sh:
        sync_ekassa_after_local_shift_open(sh)


def sync_ekassa_after_local_shift_close_by_id(shift_id) -> None:
    from apps.construction.models import CashShift

    sh = CashShift.objects.filter(pk=shift_id).select_related("company").first()
    if sh:
        sync_ekassa_after_local_shift_close(sh)


def sync_ekassa_after_local_shift_open(shift) -> None:
    """После commit открытия смены в CRM."""
    from apps.construction.models import CashShift

    company = getattr(shift, "company", None)
    if company is None:
        return

    cfg = get_integration(company)
    if cfg is None or not cfg.is_ready():
        return

    if CashShift.objects.filter(company_id=company.id, status=CashShift.Status.OPEN).count() != 1:
        return

    try:
        cli = EkassaHttpClient(cfg)
        cli.request_json("POST", "/api/shift_open_by_fiscal_number", json_body=_shift_body(cfg.fiscal_number))
        logger.info("eKassa shift_open OK company_id=%s", company.id)
    except EkassaConfigurationError:
        return
    except EkassaAPIError as e:
        logger.warning("eKassa shift_open failed company_id=%s: %s", company.id, e, exc_info=False)
    except Exception:
        logger.exception("eKassa shift_open unexpected error company_id=%s", company.id)


def sync_ekassa_after_local_shift_close(shift) -> None:
    """После commit закрытия смены в CRM."""
    from apps.construction.models import CashShift

    company = getattr(shift, "company", None)
    if company is None:
        return

    cfg = get_integration(company)
    if cfg is None or not cfg.is_ready():
        return

    if CashShift.objects.filter(company_id=company.id, status=CashShift.Status.OPEN).count() != 0:
        return

    try:
        cli = EkassaHttpClient(cfg)
        cli.request_json("POST", "/api/shift_close_by_fiscal_number", json_body=_shift_body(cfg.fiscal_number))
        logger.info("eKassa shift_close OK company_id=%s", company.id)
    except EkassaConfigurationError:
        return
    except EkassaAPIError as e:
        logger.warning("eKassa shift_close failed company_id=%s: %s", company.id, e, exc_info=False)
    except Exception:
        logger.exception("eKassa shift_close unexpected error company_id=%s", company.id)
