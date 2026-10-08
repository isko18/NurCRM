"""
Валидации справочников склада по итогам QA 06.10.2026: ИНН (B24), дубли складов и
контрагентов (B16), удаление склада с данными (B39).
"""
import re

from django.db.models import Q
from rest_framework import serializers

from . import models

INN_ERROR = "ИНН должен состоять из 14 цифр"


def normalize_inn(value) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def validate_inn(value) -> str:
    """Пустой ИНН допустим; иначе — ровно 14 цифр (ИНН КР)."""
    v = normalize_inn(value)
    if v and not re.fullmatch(r"\d{14}", v):
        raise serializers.ValidationError(INN_ERROR)
    return v


def normalize_phone(value) -> str:
    """
    Телефон КР к виду 996XXXXXXXXX (только цифры): «0555 12-34-56», «+996 555 123456»,
    «555123456» → «996555123456». Иные номера — просто цифры.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return ""
    if len(digits) == 10 and digits.startswith("0"):
        return "996" + digits[1:]
    if len(digits) == 9:
        return "996" + digits
    return digits


def format_phone(digits: str) -> str:
    if len(digits) == 12 and digits.startswith("996"):
        d = digits[3:]
        return f"+996 {d[:3]} {d[3:5]}-{d[5:7]}-{d[7:]}"
    return digits


def _norm_name(value) -> str:
    return " ".join(str(value or "").split()).lower()


def ensure_unique_warehouse_name(*, company, name, exclude_id=None):
    """Склад: уникальность (company, lower(trim(name))) → 400 duplicate_warehouse."""
    if company is None:
        return
    target = _norm_name(name)
    if not target:
        return
    qs = models.Warehouse.objects.filter(company=company)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    for pk, existing in qs.values_list("id", "name"):
        if _norm_name(existing) == target:
            raise serializers.ValidationError({
                "name": [f"Склад «{existing}» уже есть."],
                "detail": f"Склад «{existing}» уже есть.",
                "code": "duplicate_warehouse",
                "existing": {"id": str(pk), "name": existing},
            })


def ensure_unique_counterparty(*, company, data, instance=None, force_duplicate_name=False):
    """
    Контрагент (B16): совпадение нормализованного телефона или ИНН в компании → 400
    duplicate_counterparty; совпадение только имени → 409 counterparty_name_exists
    (создать можно с force_duplicate_name=true).
    """
    if company is None:
        return
    qs = models.Counterparty.objects.filter(company=company)
    if instance is not None:
        qs = qs.exclude(pk=instance.pk)

    phone = normalize_phone(data.get("phone") if "phone" in data else getattr(instance, "phone", ""))
    inn = normalize_inn(data.get("inn") if "inn" in data else getattr(instance, "inn", ""))
    name = data.get("name") if "name" in data else getattr(instance, "name", "")

    phone_changed = instance is None or "phone" in data and normalize_phone(instance.phone) != phone
    inn_changed = instance is None or "inn" in data and normalize_inn(instance.inn) != inn
    name_changed = instance is None or "name" in data and _norm_name(instance.name) != _norm_name(name)

    if phone and phone_changed:
        tail = phone[-9:]
        for cp in qs.filter(phone__contains=tail[-4:]).only("id", "name", "phone"):
            if normalize_phone(cp.phone) == phone:
                raise serializers.ValidationError({
                    "detail": f"Контрагент с телефоном {format_phone(phone)} уже есть: «{cp.name}».",
                    "code": "duplicate_counterparty",
                    "existing": {"id": str(cp.pk), "name": cp.name},
                })
    if inn and inn_changed:
        cp = qs.filter(inn=inn).only("id", "name").first()
        if cp is not None:
            raise serializers.ValidationError({
                "detail": f"Контрагент с ИНН {inn} уже есть: «{cp.name}».",
                "code": "duplicate_counterparty",
                "existing": {"id": str(cp.pk), "name": cp.name},
            })
    if name and name_changed and not force_duplicate_name:
        target = _norm_name(name)
        for cp in qs.filter(name__iexact=str(name).strip()).only("id", "name"):
            if _norm_name(cp.name) == target:
                exc = serializers.ValidationError({
                    "detail": f"Контрагент «{cp.name}» уже есть. Чтобы создать ещё одного с таким же "
                              "названием, передайте force_duplicate_name=true.",
                    "code": "counterparty_name_exists",
                    "existing": {"id": str(cp.pk), "name": cp.name},
                })
                exc.status_code = 409
                raise exc


def ensure_warehouse_deletable(warehouse):
    """B39: склад с товарами, документами или деньгами удалять нельзя (только архивировать)."""
    reasons = []
    if models.WarehouseProduct.objects.filter(warehouse=warehouse).exists():
        reasons.append("товары")
    if models.Document.objects.filter(Q(warehouse_from=warehouse) | Q(warehouse_to=warehouse)).exists():
        reasons.append("документы")
    if models.StockMove.objects.filter(warehouse=warehouse).exists():
        reasons.append("движения товара")
    if models.MoneyDocument.objects.filter(warehouse=warehouse).exists():
        reasons.append("денежные документы")
    if reasons:
        raise serializers.ValidationError({
            "detail": f"Нельзя удалить склад «{warehouse.name}»: у него есть {', '.join(reasons)}. "
                      "Переведите склад в архив (status=inactive).",
            "code": "warehouse_has_data",
        })


class WarehouseGuardMixin:
    """Для view складов: уникальное имя в компании и запрет удаления склада с данными."""

    def perform_create(self, serializer):
        ensure_unique_warehouse_name(company=self._company(), name=serializer.validated_data.get("name"))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        if "name" in serializer.validated_data:
            ensure_unique_warehouse_name(
                company=serializer.instance.company or self._company(),
                name=serializer.validated_data.get("name"),
                exclude_id=serializer.instance.pk,
            )
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        ensure_warehouse_deletable(instance)
        super().perform_destroy(instance)


def ean13_check_digit(first12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def generate_internal_barcode(company) -> str:
    """
    QA B30: внутренний EAN-13 «20» + 10 цифр сквозного номера + контрольная цифра.
    Диапазон 20 — in-store (GS1), не пересекается с реальными товарами; 21–29 не трогаем
    (весы). Уникален в компании (товары склада и доп. штрихкоды).
    """
    from django.db.models.functions import Substr

    base = models.WarehouseProduct.objects.filter(company=company, barcode__regex=r"^20\d{11}$")
    last = (
        base.annotate(seq=Substr("barcode", 3, 10)).order_by("-seq").values_list("seq", flat=True).first()
    )
    n = int(last) + 1 if last else 1
    while True:
        first12 = f"20{n:010d}"
        code = first12 + ean13_check_digit(first12)
        taken = (
            models.WarehouseProduct.objects.filter(company=company, barcode=code).exists()
            or models.WarehouseProductAlternateBarcode.objects.filter(
                product__company=company, barcode=code
            ).exists()
        )
        if not taken:
            return code
        n += 1
