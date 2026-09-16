import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from apps.main.models import Client
from apps.consalting.models import LeadConsalting

logger = logging.getLogger("nurcrm.consalting.lead_conversion")


def normalize_phone_kg(phone: str) -> str:
    """Нормализует телефон в формате E.164 (для Кыргызстана: 0xxx / 8xxx -> +996xxx)."""
    if not phone:
        return ""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("996") and len(digits) == 12:
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 10:
        return f"+996{digits[1:]}"
    if digits.startswith("8") and len(digits) == 10:
        return f"+996{digits[1:]}"
    if len(digits) == 9:
        return f"+996{digits}"
    if str(phone).strip().startswith("+"):
        return f"+{digits}"
    return digits


def merge_lead_into_client(client: Client, lead: LeadConsalting, user=None) -> Client:
    """
    Обновляет пустые поля клиента данными из лида (§8.3).
    Не затирает существующие заполненные поля.
    """
    update_fields = []
    
    lead_name = (lead.full_name or lead.title or "").strip()
    if (not client.full_name or client.full_name == "Клиент") and lead_name:
        client.full_name = lead_name
        update_fields.append("full_name")

    lead_phone = normalize_phone_kg(lead.phone)
    if not client.phone and lead_phone:
        client.phone = lead_phone
        update_fields.append("phone")

    lead_email = (lead.email or "").strip().lower()
    if not client.email and lead_email:
        client.email = lead_email
        update_fields.append("email")

    if not client.salesperson and (lead.owner or user):
        client.salesperson = lead.owner or user
        update_fields.append("salesperson")

    if not client.service and lead.service:
        client.service = lead.service
        update_fields.append("service")

    if update_fields:
        if hasattr(client, "updated_at"):
            update_fields.append("updated_at")
        client.save(update_fields=update_fields)

    return client


@transaction.atomic
def resolve_client_from_lead(lead: LeadConsalting, *, user=None, force_create=False, return_meta=False):
    """
    Находит существующего или создаёт нового Client из лида (§8.3).
    Идемпотентен для одного lead.id.
    """
    if lead.client_id:
        client = lead.client
        if return_meta:
            return client, False, None
        return client

    phone = normalize_phone_kg(lead.phone)
    digits = "".join(ch for ch in phone if ch.isdigit())
    email = (lead.email or "").strip().lower()

    existing = None
    if phone:
        existing = Client.objects.filter(company=lead.company, phone=phone).first()
        if not existing and len(digits) >= 9:
            existing = Client.objects.filter(company=lead.company, phone__endswith=digits[-9:]).first()

    if not existing and email:
        existing = Client.objects.filter(company=lead.company, email__iexact=email).first()

    merged = False
    warning = None

    if existing:
        client = merge_lead_into_client(existing, lead, user=user)
        merged = True
        warning = f"Найден существующий клиент: {existing.full_name} ({existing.phone or existing.email})"
    else:
        if not phone and not email and not force_create:
            raise ValidationError({
                "detail": "Сначала создайте клиента из лида или укажите телефон/email для автосоздания."
            })
        client = Client.objects.create(
            company=lead.company,
            branch=lead.branch,
            sector="consalting",
            full_name=lead.full_name or lead.title or "Клиент",
            phone=phone or "",
            email=email or "",
            salesperson=lead.owner or user,
            service=lead.service,
        )

    lead.client = client
    lead.save(update_fields=["client", "updated_at"])

    if return_meta:
        return client, merged, warning
    return client


def find_client_duplicates(company, *, phone=None, email=None):
    """
    Поиск дублей клиентов по телефону и/или email (§8.5).
    """
    matches = []
    
    phone_norm = normalize_phone_kg(phone) if phone else ""
    digits = "".join(ch for ch in phone_norm if ch.isdigit())
    email_clean = (email or "").strip().lower() if email else ""

    if phone_norm:
        p_q = Client.objects.filter(company=company, phone=phone_norm)
        if len(digits) >= 9:
            p_q = p_q | Client.objects.filter(company=company, phone__endswith=digits[-9:])
        for c in p_q.distinct():
            if c not in matches:
                matches.append(c)

    if email_clean:
        e_q = Client.objects.filter(company=company, email__iexact=email_clean)
        for c in e_q:
            if c not in matches:
                matches.append(c)

    results = []
    for c in matches:
        last_sale = c.consalting_sales.order_by("-created_at").first()
        results.append({
            "id": str(c.id),
            "full_name": c.full_name,
            "phone": c.phone,
            "email": c.email,
            "last_sale_at": last_sale.created_at.isoformat() if last_sale and last_sale.created_at else None,
        })
    return results
