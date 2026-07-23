"""
Проставить Product.seq существующим товарам (1..N по каждой компании).

Зачем: seq — монотонный уникальный порядковый номер внутри компании. По нему идёт
курсорная пагинация списка товаров (compact-list) и стабильная сортировка list-а.
У товаров, созданных до появления поля, seq = NULL, а сортировка/курсор по -seq с
NULL работает неверно («сначала новые» ломается, курсор пропускает строки). Эта
команда присваивает seq в хронологическом порядке (created_at, затем id), чтобы
порядок «сначала новые» совпадал с фактическим временем создания.

Порядок: created_at ASC, id ASC → самый старый товар получает seq=1, самый новый —
наибольший seq (в выдаче «-seq» он окажется сверху).

Запуск (сначала dry-run):
  python manage.py backfill_product_seq                        # все компании, предпросмотр
  python manage.py backfill_product_seq --commit               # все компании, запись
  python manage.py backfill_product_seq --company-id <uuid>    # одна компания по id
  python manage.py backfill_product_seq --company-name "Palma" # одна компания по названию
  python manage.py backfill_product_seq --company-email x@y.z  # по e-mail владельца/сотрудника

⚠️ Запускать один раз сразу после миграции (до массового создания товаров).
Повторный полный прогон перенумерует seq и сдвинет позиции курсора у клиентов —
без необходимости не запускайте повторно.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

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
    help = "Проставить Product.seq существующим товарам (1..N по каждой компании)."

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true",
                            help="Выполнить запись. Без него — только предпросмотр (dry-run).")
        parser.add_argument("--company-id", type=str, default=None,
                            help="UUID компании: обработать только её.")
        parser.add_argument("--company-name", type=str, default=None,
                            help="Название компании (точное совпадение): обработать только её.")
        parser.add_argument("--company-email", type=str, default=None,
                            help="E-mail владельца/сотрудника: обработать его компанию.")
        parser.add_argument("--only-null", action="store_true",
                            help="Не трогать товары, у которых seq уже проставлен; "
                                 "дописать только тем, у кого seq IS NULL (после текущего max).")

    def _resolve_target_company(self, opts):
        """Возвращает компанию по одному из --company-id / --company-name / --company-email."""
        cid = (opts.get("company_id") or "").strip()
        cname = (opts.get("company_name") or "").strip()
        email = (opts.get("company_email") or "").strip().lower()

        if cid:
            company = Company.objects.filter(id=cid).first()
            if company is None:
                raise CommandError(f"Компания с id={cid} не найдена.")
            return company

        if cname:
            matches = list(Company.objects.filter(name=cname))
            if not matches:
                raise CommandError(f"Компания с названием «{cname}» не найдена.")
            if len(matches) > 1:
                ids = ", ".join(str(c.id) for c in matches)
                raise CommandError(
                    f"Несколько компаний с названием «{cname}» ({ids}). Уточните через --company-id."
                )
            return matches[0]

        if email:
            User = get_user_model()
            try:
                user = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                raise CommandError(f"Пользователь не найден: {email}")
            company = _resolve_company(user)
            if company is None:
                raise CommandError(f"У аккаунта {email} нет компании.")
            return company

        return None

    def handle(self, *args, **opts):
        commit = opts["commit"]
        only_null = opts["only_null"]

        target = self._resolve_target_company(opts)
        if target is not None:
            companies = [target]
        else:
            company_ids = (
                Product.objects.values_list("company_id", flat=True).distinct()
            )
            companies = list(Company.objects.filter(id__in=company_ids).order_by("name"))

        if not companies:
            raise CommandError("Нет компаний с товарами.")

        self.stdout.write(
            f"Компаний к обработке: {len(companies)} | "
            f"режим: {'ЗАПИСЬ' if commit else 'DRY-RUN (без записи)'} | "
            f"{'только seq IS NULL' if only_null else 'полный ренумер по created_at'}\n"
        )

        total_products = 0
        total_changed = 0

        for company in companies:
            changed, count = self._backfill_company(company, commit, only_null)
            total_products += count
            total_changed += changed
            self.stdout.write(
                f"  {company.name} ({company.id}): товаров {count}, проставит seq у {changed}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nИтого: товаров {total_products}, проставит seq у {total_changed}."
        ))
        if not commit:
            self.stdout.write("Это был dry-run. Для записи повторите с --commit.")

    def _backfill_company(self, company, commit, only_null) -> tuple[int, int]:
        """Возвращает (сколько строк получит seq, всего товаров в компании)."""
        if only_null:
            # Дописываем только NULL-строкам, продолжая после текущего максимума —
            # уже проставленные seq не трогаем.
            null_rows = list(
                Product.objects
                .filter(company=company, seq__isnull=True)
                .order_by("created_at", "id")
                .values_list("id", flat=True)
            )
            count = Product.objects.filter(company=company).count()
            changed = len(null_rows)
            if not commit or changed == 0:
                return changed, count

            with transaction.atomic():
                _pg_advisory_lock_company(company.id)
                start = (
                    Product.objects
                    .filter(company=company, seq__isnull=False)
                    .order_by("-seq")
                    .values_list("seq", flat=True)
                    .first()
                ) or 0
                for offset, pid in enumerate(null_rows, 1):
                    Product.objects.filter(id=pid).update(seq=start + offset)
            return changed, count

        # Полный ренумер: 1..N по времени создания.
        ids = list(
            Product.objects
            .filter(company=company)
            .order_by("created_at", "id")
            .values_list("id", flat=True)
        )
        count = len(ids)
        changed = count
        if not commit or changed == 0:
            return changed, count

        with transaction.atomic():
            _pg_advisory_lock_company(company.id)
            # Фаза 1: паркуем seq в NULL (иначе временные коллизии по (company, seq)).
            Product.objects.filter(company=company).update(seq=None)
            # Фаза 2: присваиваем 1..N в хронологическом порядке.
            for i, pid in enumerate(ids, 1):
                Product.objects.filter(id=pid).update(seq=i)

        return changed, count
