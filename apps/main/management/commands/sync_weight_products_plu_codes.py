"""
Синхронизация Product.code и Product.plu с номерами PLU весов для компании пользователя.

Сопоставление по названию товара (без учёта регистра и лишних пробелов).
При дубликатах названий в таблице PLU берётся первое вхождение.

Запуск:
  python manage.py sync_weight_products_plu_codes --email kubanychbekovularbek82@gmail.com --dry-run
  python manage.py sync_weight_products_plu_codes --email kubanychbekovularbek82@gmail.com

Опции:
  --email       Email пользователя (обязательно)
  --dry-run     Только показать изменения, без записи в БД
  --code-width  Ширина кода с ведущими нулями (по умолчанию 4 → 0001)
  --skip-plu    Не трогать поле plu, только code
"""
from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.main.models import Product
from apps.users.models import Company

# PLU → наименование (из выгрузки весов)
PLU_BY_NAME: list[tuple[int, str]] = [
    (1, "банан"),
    (2, "Гречка"),
    (3, "Картошка Вес"),
    (4, "Колбаса Мусульманская Тойбосс"),
    (5, "Колбаса Салями Тойбосс"),
    (6, "Колбаса Сервелат Тойбосс"),
    (7, "Колбаса Телячья Тойбосс"),
    (8, "Лук Вес"),
    (9, "Молочные Сосиски Упаковка Тойбосс"),
    (10, "нават"),
    (11, "Огурцы вес"),
    (12, "Помидор Вес"),
    (13, "рис ак турпак"),
    (14, "рис казахстан байдала"),
    (15, "Рис Озгон"),
    (16, "рис ташкент лазер"),
    (17, "Сахар рас"),
    (18, "Сосиски Сырные Упаковка Тойбосс"),
    (19, "Топленка Барбол Вес"),
    (20, "Филе Упаковка Тойбосс вес"),
    (21, "Красный перец вес"),
    (22, "Крылышки Копченные вес"),
    (23, "Лук Вес"),
    (24, "Молочные Сосиски Упаковка Тойбосс"),
    (25, "нават вес"),
    (26, "Нежный Зиг-Заг С Кунжутом Барбол Вес"),
    (27, "Нокоот Вес"),
    (28, "Огурцы вес"),
    (29, "Окорочка копченые вес"),
    (30, "Окорочка Тойбосс Упаковка"),
    (31, "Охотничьи Сосиски Тойбосс Упаковка"),
    (32, "Печенье Сахарное Сгущенка Вес"),
    (33, "Печенье Сахарное Сэндвич С шоколадным ореховым вкусом Вес"),
    (34, "Помидор Вес"),
    (35, "Рис ак турпак вес"),
    (36, "Рис Аланга вес"),
    (37, "Рис Казахстан байдала вес"),
    (38, "Рис Озгон вес"),
    (39, "Рис Озгон красный вес"),
    (40, "Рис Ташкент лазер вес"),
    (41, "Рис Ташкент лазер вес"),
    (42, "Сахар рас вес"),
    (43, "Семичка Вес"),
    (44, "Сосиски Сырные Упаковка Тойбосс"),
    (45, "Сыр сментанковый вес"),
    (46, "Сыр Тильзитер Оригинальный 50% вес"),
    (47, "Топленка Барбол Вес"),
    (48, "Тушка Тойбосс Упаковка"),
    (49, "Филе Куриное Вес"),
    (50, "Филе Упаковка Тойбосс вес"),
    (51, "Чечевица вес"),
]


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def _resolve_company(user) -> Company | None:
    if user.company_id:
        return user.company
    try:
        return user.owned_company
    except Company.DoesNotExist:
        return None


def _format_plu_code(plu: int, width: int) -> str:
    return str(int(plu)).zfill(width)


def _build_unique_plu_map() -> dict[str, int]:
    """Первое вхождение названия → PLU; дубликаты названий пропускаются."""
    out: dict[str, int] = {}
    for plu, name in PLU_BY_NAME:
        key = _norm_name(name)
        if key not in out:
            out[key] = plu
    return out


class Command(BaseCommand):
    help = (
        "Выставить Product.code (и plu) по таблице PLU весов для компании пользователя. "
        "Сопоставление по названию товара."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            required=True,
            help="Email пользователя, например kubanychbekovularbek82@gmail.com",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Не записывать в БД, только отчёт",
        )
        parser.add_argument(
            "--code-width",
            type=int,
            default=4,
            help="Формат code с ведущими нулями (по умолчанию 4)",
        )
        parser.add_argument(
            "--skip-plu",
            action="store_true",
            help="Обновлять только code, не трогать plu",
        )

    def handle(self, *args, **options):
        email = (options["email"] or "").strip().lower()
        dry_run = bool(options["dry_run"])
        code_width = max(1, int(options["code_width"] or 4))
        update_plu = not bool(options["skip_plu"])

        User = get_user_model()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise CommandError(f"Пользователь с email «{email}» не найден.")

        company = _resolve_company(user)
        if company is None:
            raise CommandError(
                f"У пользователя «{email}» нет компании (User.company / owned_company)."
            )

        plu_map = _build_unique_plu_map()
        products = list(Product.objects.filter(company=company).only("id", "name", "code", "plu", "price"))

        by_name: dict[str, list[Product]] = {}
        for product in products:
            by_name.setdefault(_norm_name(product.name), []).append(product)

        planned: list[tuple[Product, str, int]] = []
        not_found: list[tuple[int, str]] = []
        ambiguous: list[tuple[int, str, list[str]]] = []
        skipped_duplicate_rows: list[tuple[int, str]] = []
        seen_product_ids: set = set()

        duplicate_name_keys = {
            _norm_name(name)
            for plu, name in PLU_BY_NAME
            if sum(1 for p, n in PLU_BY_NAME if _norm_name(n) == _norm_name(name)) > 1
        }

        for plu, raw_name in PLU_BY_NAME:
            key = _norm_name(raw_name)
            matches = by_name.get(key, [])

            if not matches:
                not_found.append((plu, raw_name))
                continue

            if len(matches) > 1:
                ambiguous.append((plu, raw_name, [str(p.id) for p in matches]))
                continue

            product = matches[0]
            if product.id in seen_product_ids:
                skipped_duplicate_rows.append((plu, raw_name))
                continue

            new_code = _format_plu_code(plu, code_width)
            planned.append((product, new_code, plu))
            seen_product_ids.add(product.id)

        self.stdout.write(f"Компания: {company.name} ({company.id})")
        self.stdout.write(f"Товаров в компании: {len(products)}")
        self.stdout.write(f"Уникальных PLU в таблице: {len(plu_map)}")
        self.stdout.write(f"К обновлению: {len(planned)}")

        for product, new_code, plu in planned:
            old_code = product.code or "—"
            old_plu = product.plu if product.plu is not None else "—"
            plu_part = f", plu {old_plu} → {plu}" if update_plu else ""
            self.stdout.write(
                f"  PLU {plu:>2}: «{product.name}» | code {old_code} → {new_code}{plu_part}"
            )

        if skipped_duplicate_rows:
            self.stdout.write(self.style.WARNING("\nПропущены повторные строки таблицы (товар уже сопоставлен):"))
            for plu, name in skipped_duplicate_rows:
                self.stdout.write(f"  PLU {plu}: «{name}»")

        if duplicate_name_keys:
            self.stdout.write(
                self.style.WARNING(
                    "\nВ таблице есть одинаковые названия с разными PLU — "
                    "для CRM используется PLU первой строки."
                )
            )

        if not_found:
            self.stdout.write(self.style.WARNING(f"\nНе найдены в CRM ({len(not_found)}):"))
            for plu, name in not_found:
                self.stdout.write(f"  PLU {plu}: «{name}»")

        if ambiguous:
            self.stdout.write(self.style.ERROR(f"\nНеоднозначное совпадение по названию ({len(ambiguous)}):"))
            for plu, name, ids in ambiguous:
                self.stdout.write(f"  PLU {plu}: «{name}» → ids: {', '.join(ids)}")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("\nDry-run: изменения не сохранены."))
            return

        if not planned:
            raise CommandError("Нечего обновлять.")

        if ambiguous:
            raise CommandError(
                "Есть неоднозначные совпадения — исправьте дубликаты названий в CRM и повторите."
            )

        target_ids = [p.id for p, _, _ in planned]
        target_codes = [code for _, code, _ in planned]
        target_plu_values = [plu for _, _, plu in planned]

        with transaction.atomic():
            # Снять code/plu у обновляемых и у чужих товаров с теми же значениями.
            Product.objects.filter(id__in=target_ids).update(code="", plu=None)

            other_with_code = Product.objects.filter(
                company=company, code__in=target_codes
            ).exclude(id__in=target_ids)
            if other_with_code.exists():
                cleared = other_with_code.update(code="")
                self.stdout.write(
                    self.style.WARNING(
                        f"Сброшен code у {cleared} других товаров (конфликт code/PLU)."
                    )
                )

            if update_plu:
                other_with_plu = (
                    Product.objects.filter(company=company, plu__in=target_plu_values)
                    .exclude(id__in=target_ids)
                )
                if other_with_plu.exists():
                    cleared = other_with_plu.update(plu=None)
                    self.stdout.write(
                        self.style.WARNING(f"Сброшен plu у {cleared} других товаров (конфликт PLU).")
                    )

            updated = 0
            for product, new_code, plu in planned:
                update_fields = ["code"]
                product.code = new_code
                if update_plu:
                    product.plu = plu
                    update_fields.append("plu")
                product.save(update_fields=update_fields)
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"\nОбновлено товаров: {updated} (code{'' if update_plu else ', без plu'}).")
        )
