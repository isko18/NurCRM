
from rest_framework import serializers


def _active_branch(serializer: serializers.Serializer):
    """
    Активный филиал:

      1) "жёстко" назначенный филиал пользователя
         (user.primary_branch() / user.primary_branch / user.branch / request.branch),
         если он принадлежит компании

      2) ?branch=<uuid> в запросе (если принадлежит компании и нет жёсткого филиала)

      3) None — нет филиала, работаем по всей компании (без фильтра по branch)
    """
    req = serializer.context.get("request")
    if not req:
        return None

    user = getattr(req, "user", None)
    company = getattr(user, "owned_company", None) or getattr(user, "company", None)
    company_id = getattr(company, "id", None)

    if not user or not getattr(user, "is_authenticated", False) or not company_id:
        return None

    # ----- 1. Жёстко назначенный филиал -----
    # 1a) user.primary_branch() как метод
    primary = getattr(user, "primary_branch", None)
    if callable(primary):
        try:
            val = primary()
            if val and getattr(val, "company_id", None) == company_id:
                setattr(req, "branch", val)
                return val
        except Exception:
            pass

    # 1b) user.primary_branch как атрибут
    if primary and not callable(primary) and getattr(primary, "company_id", None) == company_id:
        setattr(req, "branch", primary)
        return primary

    # 1c) user.branch
    if hasattr(user, "branch"):
        b = getattr(user, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            setattr(req, "branch", b)
            return b

    # 1d) request.branch (если уже проставила middleware)
    if hasattr(req, "branch"):
        b = getattr(req, "branch")
        if b and getattr(b, "company_id", None) == company_id:
            return b

    # ----- 2. Разрешаем ?branch=... ТОЛЬКО если нет жёсткого филиала -----
    branch_id = None
    if hasattr(req, "query_params"):
        branch_id = req.query_params.get("branch")
    elif hasattr(req, "GET"):
        branch_id = req.GET.get("branch")

    if branch_id and branch_id.strip():
        try:
            from apps.users.models import Branch  # на случай круговой импорта
            br = Branch.objects.get(id=branch_id, company_id=company_id)
            setattr(req, "branch", br)
            return br
        except (Branch.DoesNotExist, ValueError):
            pass

    # ----- 3. Глобальный режим по компании -----
    return None

def _restrict_pk_queryset_strict(field, base_qs, company, branch):
    """
    Было: если branch None -> показываем только branch__isnull=True.

    Теперь:
      - фильтруем по company (если есть поле company),
      - по branch фильтруем ТОЛЬКО если branch не None;
      - если branch is None -> не фильтруем по branch вообще.
    """
    if not field or base_qs is None or company is None:
        return
    qs = base_qs
    if hasattr(base_qs.model, "company"):
        qs = qs.filter(company=company)
    if hasattr(base_qs.model, "branch") and branch is not None:
        qs = qs.filter(branch=branch)
    field.queryset = qs
