import threading
from uuid import UUID

_deletion_state = threading.local()


def _company_ids() -> set[UUID]:
    if not hasattr(_deletion_state, "company_ids"):
        _deletion_state.company_ids = set()
    return _deletion_state.company_ids


def mark_companies_for_deletion(company_ids) -> None:
    _company_ids().update(company_ids)


def unmark_companies_for_deletion(company_ids) -> None:
    for company_id in company_ids:
        _company_ids().discard(company_id)


def is_company_being_deleted(company_id) -> bool:
    return company_id in _company_ids()
