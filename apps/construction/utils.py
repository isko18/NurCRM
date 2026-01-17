"""
Общие утилиты для приложения construction.
Содержит функции, используемые в views, serializers, admin и других модулях.
"""
from apps.users.models import Branch


def get_company_from_user(user):
    """
    Получить компанию пользователя из различных источников.
    
    Args:
        user: Пользователь Django
        
    Returns:
        Company или None
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None

    company = getattr(user, "company", None) or getattr(user, "owned_company", None)
    if company:
        return company

    br = getattr(user, "branch", None)
    if br is not None and getattr(br, "company", None):
        return br.company

    memberships = getattr(user, "branch_memberships", None)
    if memberships is not None:
        m = memberships.select_related("branch__company").first()
        if m and m.branch and m.branch.company:
            return m.branch.company

    return None


def is_owner_like(user) -> bool:
    """
    Проверить, является ли пользователь владельцем или администратором.
    
    Args:
        user: Пользователь Django
        
    Returns:
        True если пользователь является superuser, owner или admin
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_superuser", False):
        return True

    if getattr(user, "owned_company", None):
        return True

    if getattr(user, "is_admin", False):
        return True

    role = getattr(user, "role", None)
    if role in ("owner", "admin", "OWNER", "ADMIN", "Владелец", "Администратор"):
        return True

    return False


def fixed_branch_from_user(user, company):
    """
    Получить филиал пользователя для указанной компании.
    
    Args:
        user: Пользователь Django
        company: Компания
        
    Returns:
        Branch или None
    """
    if not user or not company:
        return None

    company_id = getattr(company, "id", None)

    memberships = getattr(user, "branch_memberships", None)
    if memberships is not None:
        primary_m = (
            memberships
            .filter(is_primary=True, branch__company_id=company_id)
            .select_related("branch")
            .first()
        )
        if primary_m and primary_m.branch:
            return primary_m.branch

        any_m = (
            memberships
            .filter(branch__company_id=company_id)
            .select_related("branch")
            .first()
        )
        if any_m and any_m.branch:
            return any_m.branch

    primary = getattr(user, "primary_branch", None)
    if callable(primary):
        try:
            val = primary()
            if val and getattr(val, "company_id", None) == company_id:
                return val
        except Exception:
            pass

    if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
        return primary

    if hasattr(user, "branch"):
        b = getattr(user, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    branch_ids = getattr(user, "branch_ids", None)
    if isinstance(branch_ids, (list, tuple)) and len(branch_ids) == 1:
        try:
            return Branch.objects.get(id=branch_ids[0], company_id=company_id)
        except Branch.DoesNotExist:
            pass

    return None


def get_active_branch(request):
    """
    Получить активный филиал из запроса.
    
    Args:
        request: HTTP запрос
        
    Returns:
        Branch или None
    """
    if not request:
        return None

    user = getattr(request, "user", None)
    company = get_company_from_user(user)
    if not company:
        setattr(request, "branch", None)
        return None

    company_id = getattr(company, "id", None)

    if not is_owner_like(user):
        fixed = fixed_branch_from_user(user, company)
        setattr(request, "branch", fixed if fixed else None)
        return fixed if fixed else None

    branch_id = request.query_params.get("branch") if hasattr(request, "query_params") else request.GET.get("branch")
    if branch_id:
        try:
            br = Branch.objects.get(id=branch_id, company_id=company_id)
            setattr(request, "branch", br)
            return br
        except (Branch.DoesNotExist, ValueError):
            pass

    if hasattr(request, "branch"):
        b = getattr(request, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    setattr(request, "branch", None)
    return None
