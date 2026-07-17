"""
Перенумеровать PLU весовых товаров в 1..N (без дыр) по каждой компании.

Зачем: в штрихкод весов идёт PLU (см. выгрузку scale-export), и он должен быть
маленьким и последовательным «начиная от 1». Реальные Product.code бывают большими
и в поле штрихкода не влезают — поэтому используется PLU, а этой командой его
приводят к чистому 1..N.

Порядок присвоения: по текущему PLU (пустые — в конец), затем по названию и id —
чтобы уже настроенные весы поменялись минимально.

⚠️ После перенумерации штрихкоды весовых товаров меняются: нужно заново
выгрузить файл (scale-export) и перезалить его в весы, а этикетки — перепечатать.

Запуск (сначала dry-run):
  python manage.py renumber_weight_plu                      # все компании, предпросмотр
  python manage.py renumber_weight_plu --commit             # все компании, запись
  python manage.py renumber_weight_plu --company-email x@y.z # одна компания
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import F

from apps.main.models import Product
from apps.users.models import Company


def _resolve_company(user):
    try:
        oc = user.owned_company
        if oc is not None:
            return oc
    except Company.DoesNotExist:
        pass
    return getattr(user, "company", None)


def _pg_advisory_lock_company(company_id):
    if connection.vendor != "postgresql" or not company_id:
        return
    key = int(str(company_id).replace("-", "")[:16], 16) & 0x7FFFFFFFFFFFFFFF
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s::bigint);", [key])


class Command(BaseCommand):
    help = "Перенумеровать PLU весовых товаров в 1..N по каждой компании."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true",
                            help="Выполнить запись. Без него — только предпросмотр (dry-run).")
        parser.add_argument("--company-email", type=str, default=None,
                            help="E-mail владельца/сотрудника: обработать только его компанию.")

    def handle(self, *args, **opts):
        commit = opts["commit"]
        email = (opts.get("company_email") or "").strip().lower()

        if email:
            User = get_user_model()
            try:
                user = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                raise CommandError(f"Пользователь не найден: {email}")
            company = _resolve_company(user)
            if company is None:
                raise CommandError(f"У аккаунта {email} нет компании.")
            companies = [company]
        else:
            # Только компании, у которых есть весовые товары.
            company_ids = (
                Product.objects.filter(is_weight=True)
                .values_list("company_id", flat=True)
                .distinct()
            )
            companies = list(Company.objects.filter(id__in=company_ids).order_by("name"))

        if not companies:
            raise CommandError("Нет компаний с весовыми товарами.")

        self.stdout.write(
            f"Компаний к обработке: {len(companies)} | "
            f"режим: {'ЗАПИСЬ' if commit else 'DRY-RUN (без записи)'}\n"
        )

        total_products = 0
        total_changed = 0

        for company in companies:
            changed, count = self._renumber_company(company, commit)
            total_products += count
            total_changed += changed
            self.stdout.write(
                f"  {company.name} ({company.id}): товаров {count}, "
                f"поменяет PLU у {changed}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nИтого: весовых товаров {total_products}, изменится PLU у {total_changed}."
        ))
        if not commit:
            self.stdout.write("Это был dry-run. Для записи повторите с --commit.")

    def _renumber_company(self, company, commit) -> tuple[int, int]:
        """Возвращает (сколько_поменяется, всего_товаров)."""
        products = list(
            Product.objects
            .filter(company=company, is_weight=True)
            .order_by(F("plu").asc(nulls_last=True), "name", "id")
            .values("id", "plu")
        )
        count = len(products)
        # Сколько строк реально изменит PLU (для отчёта).
        changed = sum(1 for i, p in enumerate(products, 1) if p["plu"] != i)

        if not commit or changed == 0:
            return changed, count

        with transaction.atomic():
            _pg_advisory_lock_company(company.id)
            ids = [p["id"] for p in products]
            # Фаза 1: паркуем все PLU в NULL (иначе временные коллизии по (company, plu)).
            Product.objects.filter(id__in=ids).update(plu=None)
            # Фаза 2: присваиваем 1..N в нужном порядке.
            for i, pid in enumerate(ids, 1):
                Product.objects.filter(id=pid).update(plu=i)

        return changed, count
