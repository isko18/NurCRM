"""
Копирование товаров склада (warehouse) из компании одного аккаунта в компанию другого.

Источник и приёмник задаются e-mail'ами. «Компания аккаунта» = owned_company (владелец),
иначе company (сотрудник). Исходные данные не меняются — создаются копии.

Структура зеркалируется: для каждого исходного склада создаётся (или переиспользуется по
названию) склад в целевой компании; бренд/категория/группа — get_or_create по названию.
branch и supplier у копий очищаются (принадлежат другой компании).

Уникальность товара — в пределах (company, warehouse): barcode / code / plu.
  - товар с уже существующим в целевом складе barcode пропускается (идемпотентность);
  - конфликтующие code/plu обнуляются и регенерируются автоматически при save().

Запуск (сначала обязательно dry-run):
  python manage.py copy_warehouse_products \
      --source-email akmatalievazyrgal22@gmail.com \
      --target-email akmatalievazyrgal2@gmail.com

  # реальная запись:
  python manage.py copy_warehouse_products \
      --source-email akmatalievazyrgal22@gmail.com \
      --target-email akmatalievazyrgal2@gmail.com --commit

НЕ копируются (при необходимости — скажите, добавлю): изображения, упаковки (packages),
доп. штрихкоды (alternate_barcodes), характеристики (characteristics), остатки движений.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.users.models import Company
from apps.warehouse import models as m

# Скалярные поля WarehouseProduct, копируемые как есть.
SCALAR_FIELDS = [
    "article", "name", "description", "barcode", "code", "unit", "is_weight",
    "quantity", "minimum_quantity", "purchase_price", "markup_percent", "price",
    "wholesale_price", "discount_percent", "plu", "country", "status", "stock",
    "expiration_date",
]


def _resolve_company(user):
    """Компания аккаунта: владелец (owned_company), иначе членство (company)."""
    try:
        oc = user.owned_company
        if oc is not None:
            return oc
    except Company.DoesNotExist:
        pass
    return getattr(user, "company", None)


class Command(BaseCommand):
    help = "Копирует товары склада из компании одного аккаунта в компанию другого (по e-mail)."

    def add_arguments(self, parser):
        parser.add_argument("--source-email", required=True, help="E-mail аккаунта-источника.")
        parser.add_argument("--target-email", required=True, help="E-mail аккаунта-приёмника.")
        parser.add_argument(
            "--commit", action="store_true",
            help="Выполнить запись. Без него — только предпросмотр (dry-run).",
        )

    def handle(self, *args, **opts):
        User = get_user_model()
        src_email = (opts["source_email"] or "").strip().lower()
        dst_email = (opts["target_email"] or "").strip().lower()
        commit = opts["commit"]

        try:
            src_user = User.objects.get(email__iexact=src_email)
        except User.DoesNotExist:
            raise CommandError(f"Аккаунт-источник не найден: {src_email}")
        try:
            dst_user = User.objects.get(email__iexact=dst_email)
        except User.DoesNotExist:
            raise CommandError(f"Аккаунт-приёмник не найден: {dst_email}")

        src_company = _resolve_company(src_user)
        dst_company = _resolve_company(dst_user)
        if src_company is None:
            raise CommandError(f"У аккаунта {src_email} нет компании.")
        if dst_company is None:
            raise CommandError(f"У аккаунта {dst_email} нет компании.")
        if src_company.id == dst_company.id:
            raise CommandError("Источник и приёмник — одна и та же компания. Копировать нечего.")

        products = list(
            m.WarehouseProduct.objects
            .filter(company=src_company)
            .select_related("warehouse", "brand", "category", "product_group")
            .order_by("warehouse_id", "name", "id")
        )
        if not products:
            raise CommandError(f"У компании «{src_company.name}» нет товаров склада для копирования.")

        self.stdout.write(
            f"Источник: {src_email} → компания «{src_company.name}» ({src_company.id})\n"
            f"Приёмник: {dst_email} → компания «{dst_company.name}» ({dst_company.id})\n"
            f"Товаров у источника: {len(products)}\n"
            f"Режим: {'ЗАПИСЬ' if commit else 'DRY-RUN (без записи)'}\n"
        )

        try:
            with transaction.atomic():
                created, skipped = self._copy(products, dst_company, commit)
                if not commit:
                    transaction.set_rollback(True)
        except Exception as exc:  # noqa: BLE001 — показать причину и не писать частично
            raise CommandError(f"Ошибка при копировании (запись откатана): {exc}") from exc

        self.stdout.write(self.style.SUCCESS(
            f"\nСоздано копий: {created} | пропущено (уже есть по barcode): {skipped}"
        ))
        if not commit:
            self.stdout.write("Это был dry-run. Для записи повторите с --commit.")

    def _copy(self, products, dst_company, commit):
        # Кэши зеркалированных справочников целевой компании.
        wh_cache: dict = {}       # src_warehouse_id -> target Warehouse
        brand_cache: dict = {}    # name -> target brand
        cat_cache: dict = {}      # name -> target category
        group_cache: dict = {}    # (target_wh_id, name) -> target group
        # Занятые уникальные значения в целевых складах: wh_id -> {"barcode","code","plu"}
        taken: dict = {}

        created = 0
        skipped = 0

        def target_warehouse(src_wh):
            if src_wh.id in wh_cache:
                return wh_cache[src_wh.id]
            name = src_wh.name or "Склад"
            wh = m.Warehouse.objects.filter(company=dst_company, name=name).first()
            if wh is None:
                wh = m.Warehouse(
                    company=dst_company,
                    branch=None,
                    name=name,
                    location=src_wh.location or "—",
                    status=src_wh.status,
                )
                if commit:
                    wh.save()
            wh_cache[src_wh.id] = wh
            return wh

        def target_brand(src_brand):
            if src_brand is None:
                return None
            key = src_brand.name
            if key in brand_cache:
                return brand_cache[key]
            obj = m.WarehouseProductBrand.objects.filter(company=dst_company, name=key).first()
            if obj is None:
                obj = m.WarehouseProductBrand(company=dst_company, branch=None, name=key)
                if commit:
                    obj.save()
            brand_cache[key] = obj
            return obj

        def target_category(src_cat):
            if src_cat is None:
                return None
            key = src_cat.name
            if key in cat_cache:
                return cat_cache[key]
            obj = m.WarehouseProductCategory.objects.filter(company=dst_company, name=key).first()
            if obj is None:
                obj = m.WarehouseProductCategory(company=dst_company, branch=None, name=key)
                if commit:
                    obj.save()
            cat_cache[key] = obj
            return obj

        def target_group(src_group, dst_wh):
            if src_group is None:
                return None
            key = (getattr(dst_wh, "id", None), src_group.name)
            if key in group_cache:
                return group_cache[key]
            obj = None
            if getattr(dst_wh, "id", None) is not None:
                obj = m.WarehouseProductGroup.objects.filter(
                    company=dst_company, warehouse=dst_wh, name=src_group.name,
                ).first()
            if obj is None:
                obj = m.WarehouseProductGroup(
                    company=dst_company, branch=None, warehouse=dst_wh,
                    name=src_group.name, parent=None,
                )
                if commit:
                    obj.save()
            group_cache[key] = obj
            return obj

        def taken_for(wh):
            wh_id = getattr(wh, "id", None)
            if wh_id not in taken:
                if wh_id is not None:
                    rows = m.WarehouseProduct.objects.filter(company=dst_company, warehouse=wh)
                    taken[wh_id] = {
                        "barcode": set(rows.exclude(barcode__isnull=True).exclude(barcode="")
                                       .values_list("barcode", flat=True)),
                        "code": set(rows.exclude(code__isnull=True).exclude(code="")
                                    .values_list("code", flat=True)),
                        "plu": set(rows.exclude(plu__isnull=True).values_list("plu", flat=True)),
                    }
                else:
                    taken[wh_id] = {"barcode": set(), "code": set(), "plu": set()}
            return taken[wh_id]

        for p in products:
            dst_wh = target_warehouse(p.warehouse)
            used = taken_for(dst_wh)

            # Идемпотентность: товар с таким barcode уже есть в целевом складе — пропускаем.
            if p.barcode and p.barcode in used["barcode"]:
                skipped += 1
                continue

            new = m.WarehouseProduct(company=dst_company, branch=None, warehouse=dst_wh)
            for f in SCALAR_FIELDS:
                setattr(new, f, getattr(p, f))
            new.brand = target_brand(p.brand)
            new.category = target_category(p.category)
            new.product_group = target_group(p.product_group, dst_wh)
            new.supplier = None

            # Конфликтующие code/plu обнуляем — save() их регенерирует.
            if new.code and new.code in used["code"]:
                new.code = None
            if new.plu is not None and new.plu in used["plu"]:
                new.plu = None

            if commit:
                new.save()
                # Отметить занятыми фактические значения (в т.ч. сгенерированные).
                if new.barcode:
                    used["barcode"].add(new.barcode)
                if new.code:
                    used["code"].add(new.code)
                if new.plu is not None:
                    used["plu"].add(new.plu)
            else:
                # В dry-run резервируем исходные значения, чтобы счётчики были правдоподобны.
                if new.barcode:
                    used["barcode"].add(new.barcode)
                if new.code:
                    used["code"].add(new.code)
                if new.plu is not None:
                    used["plu"].add(new.plu)

            created += 1

        return created, skipped
