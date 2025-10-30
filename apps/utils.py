from django.db.models import Prefetch
from apps.main.models import ProductImage
from django.db.models import Q, Case, When, IntegerField, Value as V
from django.utils import timezone

def get_filtered_contacts(queryset, params):
    """
    Переиспользуемая функция для фильтрации контактов по параметрам.
    """
    name = params.get('name')
    email = params.get('email')
    company = params.get('company')
    department = params.get('department')

    if name:
        queryset = queryset.filter(name__icontains=name)
    if email:
        queryset = queryset.filter(email__icontains=email)
    if company:
        queryset = queryset.filter(company__icontains=company)
    if department:
        queryset = queryset.filter(department__icontains=department)

    return queryset

product_images_prefetch = Prefetch(
    "images",
    queryset=ProductImage.objects.order_by("-is_primary", "-created_at"),
)



def compute_gift_qty(product, qty: int, *, company, branch=None, date=None, stacking=False) -> int:
    """
    Возвращает сколько штук дарим при qty.
    Логика:
      - Берём только активные PromoRule этой company (+ branch == наш или null).
      - Применяем только те, что подходят по конкретному продукту, его бренду, его категории, или общие.
      - Фильтруем по порогу min_qty (>, либо ≥ если inclusive=True).
      - Выбираем самое специфичное правило (product > brand > category > общее).
        Если stacking=True — суммируем все правила (но по умолчанию выключено).
    """
    if not qty or qty <= 0 or company is None:
        return 0

    from apps.main.models import PromoRule  # импорт тут, чтобы не ловить циклический импорт

    today = (date or timezone.localdate())

    qs = PromoRule.objects.filter(company=company, is_active=True).filter(
        Q(active_from__isnull=True) | Q(active_from__lte=today),
        Q(active_to__isnull=True)   | Q(active_to__gte=today),
    )

    # либо глобальные (branch is null), либо наш branch
    if branch is not None:
        qs = qs.filter(Q(branch__isnull=True) | Q(branch=branch))
    else:
        qs = qs.filter(branch__isnull=True)

    # проходим по порогу
    qs = qs.filter(
        Q(inclusive=True,  min_qty__lte=qty) |
        Q(inclusive=False, min_qty__lt=qty)
    )

    prod_id = getattr(product, "id", None)
    brand_id = getattr(product, "brand_id", None)
    cat_id = getattr(product, "category_id", None)

    # scope по продукту/бренду/категории/общие
    qs = qs.filter(
        Q(product__isnull=True, brand__isnull=True, category__isnull=True) |
        Q(product_id=prod_id) |
        Q(brand_id=brand_id) |
        Q(category_id=cat_id)
    )

    # чем уже правило, тем выше специфичность
    qs = qs.annotate(
        specificity=Case(
            When(product_id=prod_id, then=V(3)),
            When(brand_id=brand_id,   then=V(2)),
            When(category_id=cat_id,  then=V(1)),
            default=V(0),
            output_field=IntegerField(),
        )
    ).order_by("-specificity", "-priority", "-min_qty", "-id")

    if stacking:
        return sum(r.gift_qty for r in qs)

    top = qs.first()
    return top.gift_qty if top else 0



def _is_owner_like(user) -> bool:
    """
    кто имеет право одобрять:
    - суперюзер
    - staff
    - роль owner или admin
    """
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "is_staff", False):
        return True
    role = getattr(user, "role", None)
    if role in ("owner", "admin"):
        return True
    return False