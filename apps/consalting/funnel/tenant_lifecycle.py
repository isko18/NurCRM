import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from dateutil.relativedelta import relativedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.users.models import User, Company
from apps.main.models import Client
from apps.consalting.models import TenantSubscriptionExtension, TariffConsalting

logger = logging.getLogger("nurcrm.consalting.tenant_lifecycle")


class EmailAlreadyExists(Exception):
    pass


class TenantLinkConflict(Exception):
    """A tenant account cannot be attached to the requested client."""


@dataclass
class ProvisionResult:
    company_id: Optional[uuid.UUID] = None
    generated_password: Optional[str] = None
    skipped: bool = False
    reason: Optional[str] = None


@dataclass
class ExtensionResult:
    old_end_date: Optional[date] = None
    new_end_date: Optional[date] = None
    skipped: bool = False
    reason: Optional[str] = None


def create_company_with_owner(
    *, email: str, first_name: str, last_name: str, company_name: str,
    sector_id: Optional[int] = None, subscription_plan_id: Optional[int] = None,
    company_region: Optional[str] = None, end_date: Optional[date] = None
):
    """
    Создает новую Company и пользователя User с ролью 'owner'.
    """
    if User.objects.filter(email=email).exists():
        raise EmailAlreadyExists("Email уже зарегистрирован.")

    password = secrets.token_urlsafe(10)
    user = User.objects.create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        role="owner",
        is_active=True,
    )

    clean_name = slugify(company_name) or f"company-{uuid.uuid4().hex[:6]}"
    slug = clean_name[:70]
    idx = 1
    while Company.objects.filter(slug=slug).exists():
        slug = f"{clean_name[:65]}-{idx}"
        idx += 1

    tz = timezone.get_current_timezone()
    end_dt = timezone.make_aware(datetime.combine(end_date, time(23, 59, 59)), tz) if end_date else None

    company = Company.objects.create(
        name=company_name,
        slug=slug,
        owner=user,
        sector_id=sector_id,
        subscription_plan_id=subscription_plan_id,
        region=company_region,
        start_date=timezone.now(),
        end_date=end_dt,
        is_active=True,
    )

    user.company = company
    user.save(update_fields=["company"])

    return company, user, password


def resolve_provision_sector_id(*, override=None, tariff=None):
    """
    Резолв сектора для провижна CRM-аккаунта (ТЗ 11 §4.2):
    1. body.crm_sector (явный override) — если валиден
    2. tariff.crm_sector_id — если задан
    3. settings.CONSULTING_DEFAULT_CRM_SECTOR_ID (= id сектора «Маркет» / «Магазин»)
    4. Sector: slug='market' / name='маркет' / name='магазин'
    5. иначе — ValidationError
    """
    from apps.users.models import Sector

    if override:
        try:
            sec = Sector.objects.filter(id=override).first()
            if sec:
                return sec.id
        except Exception:
            pass
        raise ValidationError({"detail": "Неизвестный сектор."})

    if tariff and getattr(tariff, "crm_sector_id", None):
        return tariff.crm_sector_id

    default_id = getattr(settings, "CONSULTING_DEFAULT_CRM_SECTOR_ID", None)
    if default_id:
        try:
            sec = Sector.objects.filter(id=default_id).first()
            if sec:
                return sec.id
        except Exception:
            pass

    market = (
        Sector.objects.filter(name__iexact="маркет").first()
        or Sector.objects.filter(name__iexact="магазин").first()
        or Sector.objects.filter(name__iexact="market").first()
        or Sector.objects.first()
    )
    if not market:
        market, _ = Sector.objects.get_or_create(name="Маркет")
    if market:
        return market.id

    raise ValidationError({
        "detail": "Не задан сектор CRM-аккаунта. Заполните tariff.crm_sector_id или CONSULTING_DEFAULT_CRM_SECTOR_ID."
    })


def provision_tenant_account(
    *, client: Client, sale=None, lead=None, tariff: Optional[TariffConsalting] = None, actor=None, crm_sector=None
) -> ProvisionResult:
    """
    Автоматическое создание CRM-аккаунта (tenant) для клиента (§10.3, ТЗ 11).
    Идемпотентно: если аккаунт уже создан, возвращает существующий.
    """
    if not client:
        return ProvisionResult(skipped=True, reason="no_client")

    nur_co_id = getattr(settings, "NUR_CONSULTING_COMPANY_ID", None)
    if nur_co_id and str(client.company_id) != str(nur_co_id):
        return ProvisionResult(skipped=True, reason="not_nur_consulting_company")

    if client.nur_company_id:
        client.provision_status = Client.ProvisionStatus.CREATED
        client.save(update_fields=["provision_status"])
        return ProvisionResult(skipped=True, company_id=client.nur_company_id)

    email = (lead.email if lead and getattr(lead, "email", None) else None) or client.email
    if not email:
        client.provision_status = Client.ProvisionStatus.FAILED
        client.provision_error = "Укажите email клиента для создания аккаунта."
        client.save(update_fields=["provision_status", "provision_error"])
        return ProvisionResult(skipped=True, reason="missing_email")

    try:
        sector_id = resolve_provision_sector_id(override=crm_sector, tariff=tariff)
    except ValidationError as e:
        client.provision_status = Client.ProvisionStatus.FAILED
        err_msg = e.message_dict.get("detail", str(e)) if hasattr(e, "message_dict") else (e.messages[0] if hasattr(e, "messages") else str(e))
        client.provision_error = str(err_msg)
        client.save(update_fields=["provision_status", "provision_error"])
        raise e

    plan_id = tariff.crm_subscription_plan_id if tariff else None
    access_days = tariff.initial_access_days if (tariff and tariff.initial_access_days) else 30
    first_name = (lead.first_name if lead and getattr(lead, "first_name", None) else None) or (client.full_name.split()[0] if client.full_name else "")
    last_name = (lead.last_name if lead and getattr(lead, "last_name", None) else None) or (" ".join(client.full_name.split()[1:]) if client.full_name and len(client.full_name.split()) > 1 else "")
    comp_name = client.llc or client.enterprise or client.full_name or "Компания клиента"
    reg = getattr(lead, "company_region", None)

    try:
        with transaction.atomic():
            company, user, password = create_company_with_owner(
                email=email,
                first_name=first_name,
                last_name=last_name,
                company_name=comp_name,
                sector_id=sector_id,
                subscription_plan_id=plan_id,
                company_region=reg,
                end_date=timezone.localdate() + timedelta(days=access_days),
            )
            client.nur_company = company
            client.provision_status = Client.ProvisionStatus.CREATED
            client.provisioned_at = timezone.now()
            client.provision_error = ""
            client.save(update_fields=["nur_company", "provision_status", "provisioned_at", "provision_error"])
    except EmailAlreadyExists:
        existing_company = (
            Company.objects.select_related("owner", "sector")
            .filter(owner__email__iexact=email)
            .first()
        )
        client.provision_status = Client.ProvisionStatus.FAILED
        client.provision_error = (
            "У этого email уже есть аккаунт NurCRM. Привяжите его вместо создания нового."
        )
        client.save(update_fields=["provision_status", "provision_error"])
        raise ValidationError({
            "detail": client.provision_error,
            "existing_company": {
                "nur_company_id": str(existing_company.id),
                "company_name": existing_company.name,
                "owner_email": existing_company.owner.email,
                "sector": {
                    "id": str(existing_company.sector_id),
                    "name": existing_company.sector.name,
                } if existing_company and existing_company.sector_id else None,
                "end_date": (
                    existing_company.end_date.date().isoformat()
                    if existing_company and existing_company.end_date else None
                ),
            } if existing_company else None,
        })
    except Exception as e:
        client.provision_status = Client.ProvisionStatus.FAILED
        err_text = f"Не удалось инициализировать компанию: {e}"
        client.provision_error = err_text
        client.save(update_fields=["provision_status", "provision_error"])
        return ProvisionResult(skipped=True, reason=err_text)

    logger.info("Provisioned tenant company %s for client %s by %s", company.id, client.id, actor)
    return ProvisionResult(company_id=company.id, generated_password=password)


@transaction.atomic
def link_existing_tenant(*, client: Client, nur_company_id, actor=None) -> Client:
    """Attach a client to an existing tenant without changing its owner or password."""
    if client.nur_company_id:
        if str(client.nur_company_id) == str(nur_company_id):
            return client
        raise TenantLinkConflict("У клиента уже есть привязанный аккаунт. Сначала отвяжите текущий.")

    try:
        company = Company.objects.select_related("owner", "sector", "subscription_plan").filter(
            id=nur_company_id
        ).first()
    except (ValueError, ValidationError):
        company = None
    if not company:
        raise ValidationError({"detail": "Компания не найдена."})

    if Client.objects.filter(company=client.company, nur_company=company).exclude(id=client.id).exists():
        raise TenantLinkConflict("Этот аккаунт уже привязан к другому клиенту консалтинга.")

    client.nur_company = company
    client.provision_status = Client.ProvisionStatus.CREATED
    client.provisioned_at = timezone.now()
    client.provision_error = ""
    client.save(update_fields=["nur_company", "provision_status", "provisioned_at", "provision_error"])
    logger.info("Linked existing tenant company %s to client %s by %s", company.id, client.id, actor)
    return client


def resolve_new_end_date(company: Company, period: str, reference_date: Optional[date] = None) -> date:
    """
    Вычисляет новую дату окончания доступа при продлении подписки (§10.4).
    """
    step = relativedelta(months=1) if period == "month" else relativedelta(years=1)
    today = reference_date or timezone.localdate()
    if company.end_date:
        comp_end = timezone.localdate(company.end_date) if isinstance(company.end_date, timezone.datetime) else company.end_date
    else:
        comp_end = None

    base = comp_end if comp_end and comp_end >= today else today
    return base + step


@transaction.atomic
def extend_tenant_subscription(*, client: Client, subscription_payment, actor=None) -> ExtensionResult:
    """
    Продлевает Company.end_date для привязанной CRM-компании клиента при оплате абонентки (§10.4).
    """
    if not client or not client.nur_company:
        return ExtensionResult(skipped=True, reason="no_nur_company")

    company = client.nur_company
    period = getattr(subscription_payment.subscription, "period", "month") if subscription_payment and hasattr(subscription_payment, "subscription") else "month"

    old_end = timezone.localdate(company.end_date) if isinstance(company.end_date, timezone.datetime) else company.end_date
    new_end = resolve_new_end_date(company, period)

    tz = timezone.get_current_timezone()
    company.end_date = timezone.make_aware(datetime.combine(new_end, time(23, 59, 59)), tz)
    company.save(update_fields=["end_date"])

    TenantSubscriptionExtension.objects.create(
        company=company,
        consalting_client=client,
        subscription_payment=subscription_payment,
        old_end_date=old_end,
        new_end_date=new_end,
        period=period,
        extended_by=actor,
    )

    logger.info("Extended tenant company %s subscription from %s to %s by %s", company.id, old_end, new_end, actor)
    return ExtensionResult(old_end_date=old_end, new_end_date=new_end)
