import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")  # <-- поменяй на свой путь
django.setup()

from django.db import transaction
from django.db.models import Q
from decimal import Decimal

from apps.users.models import User, Company, Branch
from apps.main.models import Product, ProductBrand, ProductCategory  # <-- поправь путь, где у тебя эти модели


SRC_EMAIL = "DionFlowers1@gmail.com"
DST_EMAIL = "DionFlowers@gmail.com"

ZERO = Decimal("0.00")


def _get_company_by_user_email(email: str) -> Company:
    u = User.objects.get(email__iexact=email)
    # в твоей схеме владелец компании = owner (OneToOne)
    owned = getattr(u, "owned_company", None)
    if owned:
        return owned
    if u.company_id:
        return u.company
    raise ValueError(f"У пользователя {email} не найдена company/owned_company")


def _map_branch(src_branch: Branch, dst_company: Company):
    if not src_branch:
        return None
    # 1) по code, 2) по name — иначе None (глобальный товар)
    if src_branch.code:
        b = Branch.objects.filter(company=dst_company, code=src_branch.code).first()
        if b:
            return b
    return Branch.objects.filter(company=dst_company, name=src_branch.name).first()


@transaction.atomic
def перенос_товаров_без_количества():
    src_company = _get_company_by_user_email(SRC_EMAIL)
    dst_company = _get_company_by_user_email(DST_EMAIL)

    # кеши чтобы не долбить базу
    brand_cache = {}     # key: (src_brand_id, dst_branch_id) -> dst_brand
    cat_cache = {}       # key: (src_cat_id, dst_branch_id) -> dst_cat
    branch_cache = {}    # key: src_branch_id -> dst_branch

    def ensure_brand(src_brand: ProductBrand, dst_branch):
        if not src_brand:
            return None

        key = (str(src_brand.id), str(getattr(dst_branch, "id", None)))
        if key in brand_cache:
            return brand_cache[key]

        # сначала переносим parent (если есть)
        dst_parent = None
        if src_brand.parent_id:
            dst_parent = ensure_brand(src_brand.parent, dst_branch)

        # ищем по (company, branch, name)
        qs = ProductBrand.objects.filter(company=dst_company, name=src_brand.name, branch=dst_branch)
        obj = qs.first()
        if not obj:
            obj = ProductBrand.objects.create(
                company=dst_company,
                branch=dst_branch,
                name=src_brand.name,
                parent=dst_parent,
            )
        else:
            # если нашли, но parent не совпадает — можно подтянуть (не обязательно)
            if dst_parent and obj.parent_id != dst_parent.id:
                obj.parent = dst_parent
                obj.save(update_fields=["parent"])

        brand_cache[key] = obj
        return obj

    def ensure_category(src_cat: ProductCategory, dst_branch):
        if not src_cat:
            return None

        key = (str(src_cat.id), str(getattr(dst_branch, "id", None)))
        if key in cat_cache:
            return cat_cache[key]

        dst_parent = None
        if src_cat.parent_id:
            dst_parent = ensure_category(src_cat.parent, dst_branch)

        qs = ProductCategory.objects.filter(company=dst_company, name=src_cat.name, branch=dst_branch)
        obj = qs.first()
        if not obj:
            obj = ProductCategory.objects.create(
                company=dst_company,
                branch=dst_branch,
                name=src_cat.name,
                parent=dst_parent,
            )
        else:
            if dst_parent and obj.parent_id != dst_parent.id:
                obj.parent = dst_parent
                obj.save(update_fields=["parent"])

        cat_cache[key] = obj
        return obj

    src_qs = (
        Product.objects
        .filter(company=src_company)
        .select_related("branch", "brand", "brand__parent", "category", "category__parent")
        .order_by("created_at")
    )

    created = 0
    updated = 0
    skipped = 0

    for p in src_qs.iterator(chunk_size=500):
        # филиал
        if p.branch_id not in branch_cache:
            branch_cache[p.branch_id] = _map_branch(p.branch, dst_company) if p.branch_id else None
        dst_branch = branch_cache[p.branch_id]

        # scope для brand/category:
        # если src бренд/категория филиальные — берём dst_branch, иначе None
        dst_brand_branch = dst_branch if (p.brand and p.brand.branch_id) else None
        dst_cat_branch = dst_branch if (p.category and p.category.branch_id) else None

        dst_brand = ensure_brand(p.brand, dst_brand_branch) if p.brand_id else None
        dst_cat = ensure_category(p.category, dst_cat_branch) if p.category_id else None

        # barcode: переносим только если в целевой компании не занят
        barcode = (p.barcode or "").strip() or None
        if barcode and Product.objects.filter(company=dst_company, barcode=barcode).exists():
            barcode = None

        # создаём новый товар
        try:
            new_p = Product(
                company=dst_company,
                branch=dst_branch,
                kind=p.kind,

                # не тащим client/created_by (обычно это чужая компания)
                client=None,
                created_by=None,

                code="",          # пусть автогенерация
                plu=None,         # пусть автогенерация (если is_weight)

                article=p.article,
                name=p.name,
                description=p.description,
                barcode=barcode,

                brand=dst_brand,
                category=dst_cat,

                unit=p.unit,
                is_weight=p.is_weight,

                # ВАЖНО: "без кол-во" -> quantity = 0
                quantity=ZERO,

                purchase_price=p.purchase_price,
                markup_percent=p.markup_percent,
                price=p.price,
                discount_percent=p.discount_percent,

                country=p.country,
                status=p.status,
                stock=p.stock,

                date=p.date,
                expiration_date=p.expiration_date,
            )
            new_p.save()
            created += 1

        except Exception as e:
            skipped += 1
            print(f"SKIP: {p.id} / {p.name} -> {e}")

    print(f"ГОТОВО ✅  Создано: {created}, Пропущено: {skipped}")


перенос_товаров_без_количества()
