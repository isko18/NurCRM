"""
Синхронизация Product.plu с номерами PLU весов для компании пользователя.

Сопоставление по названию (без учёта регистра). При нескольких товарах с одним
названием — сначала только is_weight=True, затем по цене из таблицы.

Запуск:
  python manage.py sync_weight_products_plu_codes --email kubanychbekovularbek82@gmail.com --dry-run
  python manage.py sync_weight_products_plu_codes --email kubanychbekovularbek82@gmail.com

Опции:
  --email     Email пользователя (обязательно)
  --dry-run   Только отчёт, без записи
  --also-code Дополнительно выставить code = PLU с нулями (0001) — по умолчанию НЕ трогаем code
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.main.models import Product
from apps.users.models import Company

# (PLU, название, цена из таблицы весов — для различения дублей по названию)
PLU_ROWS: list[tuple[int, str, Decimal | None]] = [
    (1, "банан", Decimal("2")),
    (2, "Гречка", Decimal("0.7")),
    (3, "Картошка Вес", Decimal("0.55")),
    (4, "Колбаса Мусульманская Тойбосс", Decimal("5.3")),
    (5, "Колбаса Салями Тойбосс", Decimal("5.3")),
    (6, "Колбаса Сервелат Тойбосс", Decimal("5.3")),
    (7, "Колбаса Телячья Тойбосс", Decimal("5.3")),
    (8, "Лук Вес", Decimal("0.25")),
    (9, "Молочные Сосиски Упаковка Тойбосс", Decimal("4.8")),
    (10, "нават", Decimal("1.4")),
    (11, "Огурцы вес", Decimal("1.2")),
    (12, "Помидор Вес", Decimal("1.3")),
    (13, "рис ак турпак", Decimal("1.4")),
    (14, "рис казахстан байдала", Decimal("0.9")),
    (15, "Рис Озгон", Decimal("2.2")),
    (16, "рис ташкент лазер", Decimal("1.9")),
    (17, "Сахар рас", Decimal("0.85")),
    (18, "Сосиски Сырные Упаковка Тойбосс", Decimal("5")),
    (19, "Topленка Барбол Вес", None),  # alias ниже
    (19, "Топленка Барбол Вес", Decimal("3.3")),
    (20, "Филе Упаковка Тойбосс вес", Decimal("4.4")),
    (21, "Красный перец вес", Decimal("3.1")),
    (22, "Крылышки Копченные вес", Decimal("6.5")),
    (23, "Лук Вес", Decimal("0.25")),
    (24, "Молочные Сосиски Упаковка Тойбосс", Decimal("4.8")),
    (25, "нават вес", Decimal("1.4")),
    (26, "Нежный Зиг-Заг С Кунжутом Барбол Вес", Decimal("4")),
    (27, "Нокоот Вес", Decimal("1.1")),
    (28, "Огурцы вес", Decimal("0.9")),
    (29, "Окорочка копченые вес", Decimal("5")),
    (30, "Окорочка Тойбосс Упаковка", Decimal("3.3")),
    (31, "Охотничьи Сосиски Тойбосс Упаковка", Decimal("5.5")),
    (32, "Печенье Сахарное Сгущенка Вес", Decimal("2.8")),
    (33, "Печенье Сахарное Сэндвич С шоколадным ореховым вкусом Вес", Decimal("3.8")),
    (34, "Помидор Вес", Decimal("1.3")),
    (35, "Рис ак турпак вес", Decimal("1.4")),
    (36, "Рис Аланга вес", Decimal("1.3")),
    (37, "Рис Казахстан байдала вес", Decimal("0.9")),
    (38, "Рис Озгон вес", Decimal("2.2")),
    (39, "Рис Озгон красный вес", Decimal("1.8")),
    (40, "Рис Ташкент лазер вес", Decimal("1.9")),
    (41, "Рис Ташкент лазер вес", Decimal("1.95")),
    (42, "Сахар рас вес", Decimal("0.85")),
    (43, "Семичка Вес", Decimal("4")),
    (44, "Сосиски Сырные Упаковка Тойбосс", Decimal("5")),
    (45, "Сыр сментанковый вес", Decimal("5.8")),
    (46, "Сыр Тильзитер Оригинальный 50% вес", Decimal("5.8")),
    (47, "Топленка Барбол Вес", Decimal("3.3")),
    (48, "Тушка Тойбосс Упаковка", Decimal("3")),
    (49, "Филе Куриное Вес", Decimal("6.3")),
    (50, "Филе Упаковка Тойбосс вес", Decimal("4.4")),
    (51, "Чечевица вес", Decimal("1.2")),
]

# Убрать опечатку-алиас
PLU_ROWS = [(p, n, pr) for p, n, pr in PLU_ROWS if n != "Topленка Барбол Вес"]

# Имена без «вес» в CRM — варианты поиска
NAME_ALIASES: dict[str, list[str]] = {
    "гречка": ["гречка вес"],
    "нават": ["нават вес"],
    "рис ак турпак": ["рис ак турпак вес"],
    "рис казахстан байдала": ["рис казахстан байдала вес"],
    "рис озгон": ["рис озгон вес"],
    "рис ташкент лазер": ["рис ташкент лазер вес"],
    "сахар рас": ["сахар рас вес"],
}


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def _resolve_company(user) -> Company | None:
    if user.company_id:
        return user.company
    try:
        return user.owned_company
    except Company.DoesNotExist:
        return None


def _price_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _expected_prices(table_price: Decimal | None) -> list[Decimal]:
    """Цена в таблице может быть в сомах ×100 (2 → 200) или как в CRM."""
    if table_price is None:
        return []
    out = [table_price]
    scaled = table_price * Decimal("100")
    if scaled != table_price:
        out.append(scaled)
    return out


def _price_matches(product: Product, table_price: Decimal | None, tol: Decimal = Decimal("1.5")) -> bool:
    expected = _expected_prices(table_price)
    if not expected:
        return False
    p = _price_decimal(product.price)
    if p is None:
        return False
    return any(abs(p - e) <= tol for e in expected)


def _pick_product(
    candidates: list[Product],
    target_plu: int,
    table_price: Decimal | None,
) -> Product | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    weighted = [p for p in candidates if p.is_weight]
    pool = weighted if weighted else candidates

    if len(pool) == 1:
        return pool[0]

    by_plu = [p for p in pool if p.plu == target_plu]
    if len(by_plu) == 1:
        return by_plu[0]

    if table_price is not None:
        by_price = [p for p in pool if _price_matches(p, table_price)]
        if len(by_price) == 1:
            return by_price[0]

    return None


def _find_candidates(by_name: dict[str, list[Product]], raw_name: str) -> list[Product]:
    key = _norm_name(raw_name)
    matches = list(by_name.get(key, []))
    if matches:
        return matches

    for alias in NAME_ALIASES.get(key, []):
        alias_matches = by_name.get(_norm_name(alias), [])
        if alias_matches:
            return list(alias_matches)

    # «Гречка» → единственный is_weight с «гречк» в названии
    needle = key.replace(" вес", "").strip()
    if len(needle) >= 4:
        contains = [
            ps[0]
            for nk, ps in by_name.items()
            if needle in nk and len(ps) == 1 and ps[0].is_weight
        ]
        if len(contains) == 1:
            return contains

    return []


def _build_plu_jobs() -> list[tuple[int, str, Decimal | None]]:
    """Уникальные PLU: первое вхождение названия в таблице."""
    seen_plu: set[int] = set()
    seen_name: set[str] = set()
    jobs: list[tuple[int, str, Decimal | None]] = []
    for plu, name, price in PLU_ROWS:
        if plu in seen_plu:
            continue
        key = _norm_name(name)
        if key in seen_name:
            continue
        seen_plu.add(plu)
        seen_name.add(key)
        jobs.append((plu, name, price))
    return jobs


class Command(BaseCommand):
    help = "Выставить Product.plu по таблице PLU весов (code не меняется, если не указан --also-code)."

    def add_arguments(self, parser):
        parser.add_argument("--email", type=str, required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--also-code",
            action="store_true",
            help="Дополнительно записать code = PLU с ведущими нулями (0001)",
        )
        parser.add_argument("--code-width", type=int, default=4)

    def handle(self, *args, **options):
        email = (options["email"] or "").strip().lower()
        dry_run = bool(options["dry_run"])
        also_code = bool(options["also_code"])
        code_width = max(1, int(options["code_width"] or 4))

        User = get_user_model()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            raise CommandError(f"Пользователь «{email}» не найден.")

        company = _resolve_company(user)
        if not company:
            raise CommandError(f"У «{email}» нет компании.")

        products = list(
            Product.objects.filter(company=company).only(
                "id", "name", "code", "plu", "price", "is_weight"
            )
        )
        by_name: dict[str, list[Product]] = {}
        for product in products:
            by_name.setdefault(_norm_name(product.name), []).append(product)

        planned: list[tuple[Product, int, str | None]] = []
        not_found: list[tuple[int, str]] = []
        ambiguous: list[tuple[int, str, list[str]]] = []
        skipped_same: list[tuple[int, str]] = []
        seen_ids: set = set()

        for plu, raw_name, table_price in _build_plu_jobs():
            candidates = _find_candidates(by_name, raw_name)
            if not candidates:
                not_found.append((plu, raw_name))
                continue

            product = _pick_product(candidates, plu, table_price)
            if product is None:
                ambiguous.append((plu, raw_name, [str(p.id) for p in candidates]))
                continue

            if product.id in seen_ids:
                skipped_same.append((plu, raw_name))
                continue

            new_code = str(plu).zfill(code_width) if also_code else None
            planned.append((product, plu, new_code))
            seen_ids.add(product.id)

        self.stdout.write(f"Компания: {company.name} ({company.id})")
        self.stdout.write(f"Товаров: {len(products)} | К обновлению plu: {len(planned)}")
        if also_code:
            self.stdout.write("Режим: plu + code")
        else:
            self.stdout.write("Режим: только plu (code не меняется)")

        for product, plu, new_code in planned:
            old_plu = product.plu if product.plu is not None else "—"
            code_part = ""
            if also_code and new_code:
                code_part = f" | code {product.code or '—'} → {new_code}"
            if product.plu == plu and (not also_code or product.code == new_code):
                self.stdout.write(f"  PLU {plu:>2}: «{product.name}» — уже {plu}, пропуск")
            else:
                self.stdout.write(
                    f"  PLU {plu:>2}: «{product.name}» | plu {old_plu} → {plu}{code_part}"
                )

        if skipped_same:
            self.stdout.write(self.style.WARNING("\nДубликаты строк таблицы (товар уже в списке):"))
            for plu, name in skipped_same:
                self.stdout.write(f"  PLU {plu}: «{name}»")

        if not_found:
            self.stdout.write(self.style.WARNING(f"\nНе найдены ({len(not_found)}):"))
            for plu, name in not_found:
                self.stdout.write(f"  PLU {plu}: «{name}»")

        if ambiguous:
            self.stdout.write(self.style.WARNING(f"\nНеоднозначно — пропущены ({len(ambiguous)}):"))
            for plu, name, ids in ambiguous:
                self.stdout.write(f"  PLU {plu}: «{name}» → {len(ids)} товаров: {', '.join(ids[:5])}{'…' if len(ids) > 5 else ''}")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("\nDry-run: без изменений в БД."))
            return

        to_apply = [
            (p, plu, nc)
            for p, plu, nc in planned
            if p.plu != plu or (also_code and nc and p.code != nc)
        ]
        if not to_apply:
            self.stdout.write(self.style.SUCCESS("\nВсе plu уже актуальны."))
            return

        target_plu_values = [plu for _, plu, _ in to_apply]
        target_ids = [p.id for p, _, _ in to_apply]

        with transaction.atomic():
            Product.objects.filter(
                company=company, plu__in=target_plu_values
            ).exclude(id__in=target_ids).update(plu=None)

            updated = 0
            for product, plu, new_code in to_apply:
                product.plu = plu
                fields = ["plu"]
                if also_code and new_code:
                    Product.objects.filter(
                        company=company, code=new_code
                    ).exclude(id=product.id).update(code="")
                    product.code = new_code
                    fields.append("code")
                product.save(update_fields=fields)
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"\nОбновлено: {updated} товаров (plu)."))
        if ambiguous or not_found:
            self.stdout.write(
                self.style.WARNING(
                    "Часть PLU не применена — см. «Не найдены» / «Неоднозначно» выше."
                )
            )
