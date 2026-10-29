from rest_framework import serializers


def normalize_payment_kind(value):
    """
    Приводит способ оплаты к значениям Document.PaymentKind.
    Фронт/POS может прислать debt вместо credit.
    """
    if value is None or value == "":
        return None
    raw = str(value).strip().lower().replace("-", "_")
    if raw in ("credit", "debt", "v_dolg", "v_dolgu", "dolg", "в_долг", "вдолг"):
        return "credit"
    if raw in ("cash", "nal", "nalichnye", "наличные", "нал"):
        return "cash"
    if raw == "external":
        return "external"
    return str(value).strip()


def effective_payment_kind(value, *, default="cash"):
    normalized = normalize_payment_kind(value)
    if normalized is None:
        return default
    return normalized


def normalize_payment_method(value):
    """
    Приводит форму оплаты к значениям Document.PaymentMethod:
    наличные -> cash, безналичные (карта/перевод) -> cashless.
    """
    if value is None or value == "":
        return None
    raw = str(value).strip().lower().replace("-", "_")
    if raw in ("cash", "nal", "nalichnye", "наличные", "наличными", "нал"):
        return "cash"
    if raw in (
        "cashless", "beznal", "beznalichnye", "безналичные", "безналичными",
        "безнал", "card", "карта", "перевод", "transfer", "bank",
    ):
        return "cashless"
    return str(value).strip()


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


def ensure_system_payment_categories(company, branch=None):
    """
    Идемпотентно создаёт системные категории («Продажа», «Долги», «Закупка», …) компании.

    QA B13: системные категории — одни на компанию (branch=NULL), а не копия на каждый
    филиал: раньше владелец без выбранного филиала видел «Закупку» столько раз, сколько
    филиалов, и отчёты по категориям расходились. Аргумент branch оставлен для
    совместимости вызовов и не используется.
    Если уже есть запись компании с тем же title и без system_code — ей присваивается код.
    """
    import logging

    from django.db import transaction

    from . import models
    from apps.users.models import Company

    if company is None:
        return
    all_codes = {m.value for m in models.PaymentCategory.SystemCode}
    have = set(
        models.PaymentCategory.objects.filter(
            company=company, branch__isnull=True, system_code__isnull=False
        ).values_list("system_code", flat=True)
    )
    if all_codes <= have:
        # Быстрый путь без блокировки: всё уже создано (обычный случай).
        return
    try:
        with transaction.atomic():
            # Сериализуем создание по компании: параллельные проведения не создадут дубли.
            Company.objects.select_for_update().filter(pk=company.pk).first()
            qs = models.PaymentCategory.objects.filter(company=company, branch__isnull=True)
            for member in models.PaymentCategory.SystemCode:
                code = member.value
                title = member.label
                existing = qs.filter(system_code=code).exists()
                if existing:
                    continue
                orphan = qs.filter(system_code__isnull=True, title=title).first()
                if orphan:
                    orphan.system_code = code
                    orphan.save(update_fields=["system_code"])
                    continue
                models.PaymentCategory.objects.create(
                    company=company,
                    branch=None,
                    system_code=code,
                    title=title,
                )
    except Exception:
        logging.getLogger(__name__).exception("ensure_system_payment_categories failed for company %s", getattr(company, "pk", None))


def system_payment_category(company, code):
    """Системная категория компании по коду (создаётся при необходимости)."""
    from . import models

    if company is None:
        return None
    ensure_system_payment_categories(company)
    return (
        models.PaymentCategory.objects.filter(company=company, branch__isnull=True, system_code=code)
        .order_by("id")
        .first()
    )

