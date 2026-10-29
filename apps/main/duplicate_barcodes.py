"""
«Калькуляция», задача 3: товары компании с одинаковым штрихкодом.

Логика для GET /api/main/products/barcode-duplicates/ (ТЗ ч.12, 2.6) и его синонима
GET /api/main/products/duplicate-barcodes/ (calculator-after-stress-test/03):
    ?same_scope_only=true    только настоящие дубли (в одном филиале или в основном каталоге)

Учитываются основной штрихкод и дополнительные (alternate_barcodes).

Две разновидности групп:
  same_scope=true  — несколько товаров с одним штрихкодом в ОДНОЙ области (основной
                     каталог или один филиал). Так быть не должно: сейчас это запрещено
                     при сохранении, такие группы — старые данные, их нужно объединить.
  same_scope=false — копии одного товара в разных филиалах (создаются перемещением между
                     филиалами, у каждой свой остаток). Это допустимо по модели.
"""
from collections import defaultdict

from django.db.models import Count
from .models import Product, ProductAlternateBarcode


def find_duplicate_barcodes(products_qs, *, same_scope_only=False) -> list:
    """Группы {barcode, same_scope, products[...]} по товарам queryset (с учётом доп. штрихкодов)."""
    ids = products_qs.values("pk")
    pairs = defaultdict(dict)  # barcode -> {product_id: source}

    main_dups = (
        products_qs.exclude(barcode__isnull=True).exclude(barcode="")
        .values("barcode").annotate(n=Count("id"))
    )
    alt_codes = set(
        ProductAlternateBarcode.objects.filter(product_id__in=ids).values_list("barcode", flat=True)
    )
    candidates = {r["barcode"] for r in main_dups if r["n"] > 1}
    if alt_codes:
        # доп. штрихкод совпал с основным или с другим доп. штрихкодом
        candidates |= set(
            products_qs.filter(barcode__in=alt_codes).values_list("barcode", flat=True)
        )
        alt_counts = (
            ProductAlternateBarcode.objects.filter(product_id__in=ids)
            .values("barcode").annotate(n=Count("product_id", distinct=True))
        )
        candidates |= {r["barcode"] for r in alt_counts if r["n"] > 1}
    if not candidates:
        return []

    for pid, bc in products_qs.filter(barcode__in=candidates).values_list("pk", "barcode"):
        pairs[bc][pid] = "main"
    for pid, bc in ProductAlternateBarcode.objects.filter(
        product_id__in=ids, barcode__in=candidates
    ).values_list("product_id", "barcode"):
        pairs[bc].setdefault(pid, "alternate")

    all_ids = {pid for group in pairs.values() for pid in group}
    info = {
        p["pk"]: p
        for p in Product.objects.filter(pk__in=all_ids).values(
            "pk", "code", "name", "quantity", "status", "branch_id", "branch__name", "created_at"
        )
    }
    out = []
    for bc in sorted(pairs):
        group = pairs[bc]
        if len(group) < 2:
            continue
        scopes = [info[pid]["branch_id"] for pid in group]
        same_scope = len(set(scopes)) < len(scopes)
        if same_scope_only and not same_scope:
            continue
        products = []
        for pid, source in group.items():
            p = info[pid]
            products.append({
                "id": str(pid),
                "code": p["code"],
                "name": p["name"],
                "quantity": str(p["quantity"] if p["quantity"] is not None else "0.000"),
                "status": p["status"],
                "created_at": p["created_at"].isoformat() if p["created_at"] else None,
                "branch": str(p["branch_id"]) if p["branch_id"] else None,
                "branch_name": p["branch__name"] or "Основной склад",
                "is_alternate": source == "alternate",
            })
        products.sort(key=lambda r: r["created_at"] or "")
        out.append({"barcode": bc, "same_scope": same_scope, "products": products})
    out.sort(key=lambda g: (not g["same_scope"], g["barcode"]))
    return out
