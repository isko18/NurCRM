"""
Права на опасные складские операции (QA 06.10.2026, §2).

Проверяются в сервисах проведения (post_document / unpost_document /
post_money_document), а не во view: те же сервисы вызывают мобильное приложение,
корзины агентов и т.д. Вызов без пользователя (user=None) — системный
(команды, внутренние сценарии) и правами не ограничивается.

Право сотрудника хранится в поле User.<perm>: True/False — выдано/запрещено явно,
NULL — по умолчанию для роли (ROLE_DEFAULTS). Владелец компании может всё.
"""

POST_NEGATIVE_STOCK = "can_post_negative_stock"
UNPOST_DOCUMENTS = "can_unpost_documents"
CHANGE_DOCUMENT_DATE = "can_change_document_date"
POST_CLOSED_PERIOD = "can_post_closed_period"
CASH_NEGATIVE = "can_cash_negative"

OP_PERMISSION_FIELDS = (
    POST_NEGATIVE_STOCK,
    UNPOST_DOCUMENTS,
    CHANGE_DOCUMENT_DATE,
    POST_CLOSED_PERIOD,
    CASH_NEGATIVE,
)

# Право по умолчанию (поле сотрудника = NULL). Владелец — всегда да.
ROLE_DEFAULTS = {
    POST_NEGATIVE_STOCK: {"owner"},
    UNPOST_DOCUMENTS: {"owner", "admin"},
    CHANGE_DOCUMENT_DATE: {"owner", "admin"},
    POST_CLOSED_PERIOD: {"owner"},
    CASH_NEGATIVE: {"owner"},
}

# code и текст ответа 403 для каждого права
PERMISSION_ERRORS = {
    POST_NEGATIVE_STOCK: ("permission_negative_stock", "Недостаточно прав: проведение в минус"),
    UNPOST_DOCUMENTS: ("permission_unpost", "Недостаточно прав: отмена проведения документа"),
    CHANGE_DOCUMENT_DATE: ("permission_change_date", "Недостаточно прав: дата документа, отличная от сегодняшней"),
    POST_CLOSED_PERIOD: ("permission_closed_period", "Недостаточно прав: изменения в закрытом периоде"),
    CASH_NEGATIVE: ("permission_cash_negative", "Недостаточно прав: расход, уводящий кассу в минус"),
}


class OperationForbidden(PermissionError):
    """Нет права на операцию. Во view → 403 {"detail", "code"}."""

    status_code = 403

    def __init__(self, perm: str, detail: str = None):
        code, default_detail = PERMISSION_ERRORS.get(perm, ("permission_denied", "Недостаточно прав"))
        self.perm = perm
        self.api_code = code
        super().__init__(detail or default_detail)


class BusinessRuleError(ValueError):
    """
    Нарушение бизнес-правила с кодом для фронта. Во view → 400
    {"detail", "code", ...extra}. Наследуется от ValueError: старые обработчики
    `except ValueError` продолжают отдавать 400.
    """

    def __init__(self, detail: str, code: str, **extra):
        self.api_code = code
        self.extra = extra
        super().__init__(detail)


def is_company_owner(user) -> bool:
    if user is None:
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "role", None) == "owner":
        return True
    try:
        return getattr(user, "owned_company", None) is not None
    except Exception:
        return False


def is_warehouse_agent(user, company=None) -> bool:
    """Пользователь — активный агент склада (не сотрудник-владелец/админ)."""
    if user is None or is_company_owner(user) or getattr(user, "role", None) == "admin":
        return False
    from . import models

    qs = models.CompanyWarehouseAgent.objects.filter(
        user=user, status=models.CompanyWarehouseAgent.Status.ACTIVE
    )
    if company is not None:
        qs = qs.filter(company=company)
    return qs.exists()


def has_op_permission(user, perm: str) -> bool:
    """user=None — системный вызов, разрешено."""
    if user is None:
        return True
    if is_company_owner(user):
        return True
    explicit = getattr(user, perm, None)
    if explicit is not None:
        return bool(explicit)
    return (getattr(user, "role", None) or "") in ROLE_DEFAULTS.get(perm, set())


def require_op_permission(user, perm: str, detail: str = None):
    if not has_op_permission(user, perm):
        raise OperationForbidden(perm, detail)
