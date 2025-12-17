from rest_framework import generics, status, permissions, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime, parse_date
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from django.http import FileResponse
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from decimal import Decimal, ROUND_HALF_UP
import qrcode
from datetime import timedelta
import io, os, uuid
from django.db.models import Q, F, Value as V
from django.db.models.functions import Coalesce
from apps.users.models import Company
from apps.main.models import Cart, CartItem, Sale, Product, MobileScannerToken, Client
from .pos_serializers import (
    SaleCartSerializer, SaleItemSerializer,
    ScanRequestSerializer, AddItemSerializer,
    CheckoutSerializer, MobileScannerTokenSerializer,
    SaleListSerializer, SaleDetailSerializer, StartCartOptionsSerializer,
    CustomCartItemCreateSerializer, SaleStatusUpdateSerializer, ReceiptSerializer,
)
from apps.main.services import checkout_cart, NotEnoughStock
from apps.main.views import CompanyBranchRestrictedMixin
from django.http import Http404
from django.utils.timezone import is_aware, make_aware, get_current_timezone
from datetime import datetime, date, time as dtime
from django.db.models import Sum
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from typing import Iterable, List, Optional, Dict
from reportlab.pdfbase.ttfonts import TTFont
from apps.main.models import ManufactureSubreal, AgentSaleAllocation
from apps.main.services_agent_pos import checkout_agent_cart, AgentNotEnoughStock
from dataclasses import dataclass
from apps.main.utils_numbers import ensure_sale_doc_number
from django.core.cache import cache
from apps.users.models import Roles, User, Company
import requests
from django.conf import settings
from apps.construction.models import Cashbox, CashShift
from apps.utils import _is_owner_like

try:
    from apps.main.models import ClientDeal, DealInstallment
except Exception:
    ClientDeal = None
    DealInstallment = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FONTS_DIR = os.path.join(BASE_DIR, "fonts")

pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(FONTS_DIR, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf")))


def _q2(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_money(x: Decimal) -> str:
    return f"{_q2(x):.2f}"


Q2 = Decimal("0.01")


def q2(x: Optional[Decimal]) -> Decimal:
    return (x or Decimal("0.00")).quantize(Q2, rounding=ROUND_HALF_UP)


def fmt(x: Optional[Decimal]) -> str:
    return f"{q2(x):,.2f}".replace(",", " ").replace("\xa0", " ")


Q2 = Decimal("0.01")


def money(x: Optional[Decimal]) -> Decimal:
    return (x or Decimal("0")).quantize(Q2, rounding=ROUND_HALF_UP)


def fmt(x: Optional[Decimal]) -> str:
    return f"{money(x):.2f}"


def _aware(dt_or_date, end=False):
    tz = get_current_timezone()
    if isinstance(dt_or_date, datetime):
        return dt_or_date if is_aware(dt_or_date) else make_aware(dt_or_date, tz)
    if isinstance(dt_or_date, date):
        t = dtime(23, 59, 59) if end else dtime(0, 0, 0)
        return make_aware(datetime.combine(dt_or_date, t), tz)
    return None


def _safe(s) -> str:
    return s if (s is not None and str(s).strip()) else "—"


def register_fonts_if_needed():
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))
    except Exception:
        pass


register_fonts_if_needed()


@dataclass
class Entry:
    date: datetime
    desc: str
    debit: Decimal
    credit: Decimal


def _safe(v):
    return v if (v is not None and str(v).strip()) else "—"


def _party_lines(
    title,
    name,
    inn=None,
    okpo=None,
    score=None,
    bik=None,
    addr=None,
    phone=None,
    email=None,
):
    return [
        title,
        name,
        f"ИНН: {_safe(inn)}   ОКПО: {_safe(okpo)}",
        f"Р/с: {_safe(score)}   БИК: {_safe(bik)}",
        f"Адрес: {_safe(addr)}",
        f"Тел.: {_safe(phone)}",
    ]


def _parse_scale_barcode(barcode: str):
    """
    Парсим EAN-13 весовой штрихкод формата:
    PP CCCCC WWWWW K

    - PP     : префикс (20/21/22/... — тут не валидируем жёстко)
    - CCCCC  : ПЛУ товара (5 цифр)
    - WWWWW  : вес в граммах (5 цифр, 00312 -> 0.312 кг)
    - K      : контрольная цифра (игнорируем)

    Возвращаем dict или None, если не похоже на весовой штрих.
    """
    if not barcode or len(barcode) != 13 or not barcode.isdigit():
        return None

    prefix = barcode[0:2]
    plu_digits = barcode[2:7]
    weight_digits = barcode[7:12]

    try:
        plu_int = int(plu_digits)
        weight_raw = int(weight_digits)
    except ValueError:
        return None

    weight_kg = weight_raw / 1000.0

    return {
        "prefix": prefix,
        "plu": plu_int,
        "weight_raw": weight_raw,
        "weight_kg": weight_kg,
    }
    
def _resolve_pos_cashbox(company, branch, cashbox_id=None):
    """
    Правило:
    - если cashbox_id передали -> берём её (только этой компании/филиала)
    - иначе -> берём последнюю созданную кассу этого филиала
    - если в филиале нет кассы -> пробуем глобальную (branch NULL)
    """
    qs = Cashbox.objects.filter(company=company)

    if branch is None:
        qs_branch = qs.filter(branch__isnull=True)
    else:
        qs_branch = qs.filter(branch=branch)

    if cashbox_id:
        cb = qs_branch.filter(id=cashbox_id).first()
        if not cb:
            raise ValidationError({"cashbox_id": "Касса не найдена или не принадлежит этому филиалу."})
        return cb

    cb = qs_branch.order_by("-created_at").first()
    if cb:
        return cb

    # fallback на глобальную кассу компании
    cb = qs.filter(branch__isnull=True).order_by("-created_at").first()
    return cb

@transaction.atomic
def _ensure_open_shift(*, company, branch, cashier, cashbox, opening_cash=None):
    opening_cash = Decimal(opening_cash or "0.00")

    shift = (
        CashShift.objects
        .select_for_update()
        .filter(company=company, cashbox=cashbox, status=CashShift.Status.OPEN)
        .order_by("-opened_at")
        .first()
    )
    if shift:
        return shift

    return CashShift.objects.create(
        company=company,
        branch=branch,
        cashbox=cashbox,
        cashier=cashier,
        opening_cash=opening_cash,
        status=CashShift.Status.OPEN,
    )
    
class ClientReconciliationClassicAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, client_id, *args, **kwargs):
        company = request.user.company
        client = get_object_or_404(Client, id=client_id, company=company)

        source = (request.query_params.get("source") or "both").lower()
        currency = request.query_params.get("currency") or "KGS"

        s = request.query_params.get("start")
        e = request.query_params.get("end")
        if not s or not e:
            return self._error_pdf("Укажите параметры start и end (YYYY-MM-DD).")

        start_dt = parse_datetime(s) or parse_date(s)
        end_dt = parse_datetime(e) or parse_date(e)
        if not start_dt or not end_dt:
            return self._error_pdf("Неверный формат дат. Используйте YYYY-MM-DD или ISO datetime.")

        start_dt = _aware(start_dt, end=False)
        end_dt = _aware(end_dt, end=True)

        debit_before = Decimal("0.00")
        credit_before = Decimal("0.00")

        if source in ("both", "sales"):
            sales_before = (
                Sale.objects.filter(company=company, client=client, created_at__lt=start_dt).aggregate(s=Sum("total"))[
                    "s"
                ]
                or Decimal("0")
            )
            debit_before += sales_before

        if ClientDeal and source in ("both", "deals"):
            deals_before = (
                ClientDeal.objects.filter(
                    company=company,
                    client=client,
                    created_at__lt=start_dt,
                    kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT],
                ).aggregate(s=Sum("amount"))["s"]
                or Decimal("0")
            )
            debit_before += deals_before
            pre_before = (
                ClientDeal.objects.filter(company=company, client=client, created_at__lt=start_dt).aggregate(
                    s=Sum("prepayment")
                )["s"]
                or Decimal("0")
            )
            credit_before += pre_before

        if DealInstallment and source in ("both", "deals"):
            inst_before = (
                DealInstallment.objects.filter(
                    deal__company=company,
                    deal__client=client,
                    paid_on__isnull=False,
                    paid_on__lt=start_dt.date(),
                ).aggregate(s=Sum("amount"))["s"]
                or Decimal("0")
            )
            credit_before += inst_before

        opening = q2(debit_before - credit_before)

        entries: List[Dict] = []

        if source in ("both", "sales"):
            for s in (
                Sale.objects.filter(
                    company=company,
                    client=client,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                ).order_by("created_at")
            ):
                if q2(s.total) > 0:
                    entries.append(
                        dict(
                            date=s.created_at,
                            title=f"Продажа {s.id}",
                            a_debit=q2(s.total),
                            a_credit=Decimal("0.00"),
                            b_debit=Decimal("0.00"),
                            b_credit=q2(s.total),
                        )
                    )

        if ClientDeal and source in ("both", "deals"):
            for d in (
                ClientDeal.objects.filter(
                    company=company,
                    client=client,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                    kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT],
                ).order_by("created_at")
            ):
                amt = q2(d.amount)
                if amt > 0:
                    entries.append(
                        dict(
                            date=d.created_at,
                            title=f"Сделка: {d.title} ({d.get_kind_display()})",
                            a_debit=amt,
                            a_credit=Decimal("0.00"),
                            b_debit=Decimal("0.00"),
                            b_credit=amt,
                        )
                    )
            for d in (
                ClientDeal.objects.filter(
                    company=company,
                    client=client,
                    prepayment__gt=0,
                    created_at__gte=start_dt,
                    created_at__lte=end_dt,
                ).order_by("created_at")
            ):
                pp = q2(d.prepayment)
                entries.append(
                    dict(
                        date=d.created_at,
                        title=f"Предоплата (сделка: {d.title})",
                        a_debit=Decimal("0.00"),
                        a_credit=pp,
                        b_debit=pp,
                        b_credit=Decimal("0.00"),
                    )
                )

        if DealInstallment and source in ("both", "deals"):
            for inst in (
                DealInstallment.objects.filter(
                    deal__company=company,
                    deal__client=client,
                    paid_on__isnull=False,
                    paid_on__gte=start_dt.date(),
                    paid_on__lte=end_dt.date(),
                )
                .select_related("deal")
                .order_by("paid_on", "number")
            ):
                amt = q2(inst.amount)
                dt = _aware(inst.paid_on, end=False)
                entries.append(
                    dict(
                        date=dt,
                        title=f"Оплата по рассрочке №{inst.number} (сделка: {inst.deal.title})",
                        a_debit=Decimal("0.00"),
                        a_credit=amt,
                        b_debit=amt,
                        b_credit=Decimal("0.00"),
                    )
                )

        entries.sort(key=lambda x: x["date"])

        totals = dict(
            a_debit=q2(sum(x["a_debit"] for x in entries) if entries else 0),
            a_credit=q2(sum(x["a_credit"] for x in entries) if entries else 0),
            b_debit=q2(sum(x["b_debit"] for x in entries) if entries else 0),
            b_credit=q2(sum(x["b_credit"] for x in entries) if entries else 0),
        )

        closing = q2(opening + totals["a_debit"] - totals["a_credit"])
        on_date = (end_dt + timedelta(days=1)).date()

        buf = io.BytesIO()
        p = canvas.Canvas(buf, pagesize=A4)
        W, H = A4

        try:
            FONT, BFONT = "DejaVu", "DejaVu-Bold"
            p.setFont(BFONT, 14)
        except Exception:
            FONT, BFONT = "Helvetica", "Helvetica-Bold"
            p.setFont(BFONT, 14)

        p.drawCentredString(W / 2, H - 20 * mm, "АКТ СВЕРКИ ВЗАИМНЫХ РАСЧЁТОВ")
        p.setFont(FONT, 11)
        p.drawCentredString(
            W / 2,
            H - 27 * mm,
            f"Период: {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}   валюта сверки {currency}",
        )

        company_name = getattr(company, "llc", None) or getattr(company, "name", str(company))
        client_name = client.llc or client.enterprise or client.full_name

        p.setFont(BFONT, 10)
        p.drawString(20 * mm, H - 38 * mm, "КОМПАНИЯ")
        p.drawString(110 * mm, H - 38 * mm, "КЛИЕНТ")
        p.setFont(FONT, 11)
        p.drawString(20 * mm, H - 44 * mm, _safe(company_name))
        p.drawString(110 * mm, H - 44 * mm, _safe(client_name))
        p.setFont(FONT, 9)
        p.drawString(
            20 * mm,
            H - 50 * mm,
            f"ИНН: {_safe(getattr(company,'inn',None))}    ОКПО: {_safe(getattr(company,'okpo',None))}",
        )
        p.drawString(
            110 * mm,
            H - 50 * mm,
            f"ИНН: {_safe(client.inn)}    ОКПО: {_safe(client.okpo)}",
        )
        p.drawString(
            20 * mm,
            H - 56 * mm,
            f"Р/с: {_safe(getattr(company,'score',None))}    БИК: {_safe(getattr(company,'bik',None))}",
        )
        p.drawString(
            110 * mm,
            H - 56 * mm,
            f"Р/с: {_safe(client.score)}    БИК: {_safe(client.bik)}",
        )
        p.drawString(20 * mm, H - 62 * mm, f"Адрес: {_safe(getattr(company,'address',None))}")
        p.drawString(110 * mm, H - 62 * mm, f"Адрес: {_safe(client.address)}")
        p.drawString(
            20 * mm,
            H - 68 * mm,
            f"Тел.: {_safe(getattr(company,'phone',None))}    E-mail: {_safe(getattr(company,'email',None))}",
        )
        p.drawString(
            110 * mm,
            H - 68 * mm,
            f"Тел.: {_safe(client.phone)}    E-mail: {_safe(client.email)}",
        )

        y = H - 78 * mm
        p.setFont(BFONT, 9)
        p.drawString(20 * mm, y, "№")
        p.drawString(28 * mm, y, "Содержание записи")
        p.drawString(100 * mm, y, _safe(company_name))
        p.drawString(148 * mm, y, _safe(client_name))

        y -= 5 * mm
        p.setFont(BFONT, 9)
        p.drawString(100 * mm, y, "Дт")
        p.drawString(118 * mm, y, "Кт")
        p.drawString(148 * mm, y, "Дт")
        p.drawString(166 * mm, y, "Кт")
        p.line(20 * mm, y - 1 * mm, 190 * mm, y - 1 * mm)
        y -= 6 * mm

        p.setFont(FONT, 9)
        a_dt = opening if opening > 0 else Decimal("0.00")
        a_kt = -opening if opening < 0 else Decimal("0.00")
        b_dt = a_kt
        b_kt = a_dt

        p.drawString(28 * mm, y, "Сальдо начальное")
        p.drawRightString(115 * mm, y, fmt(a_dt))
        p.drawRightString(133 * mm, y, fmt(a_kt))
        p.drawRightString(163 * mm, y, fmt(b_dt))
        p.drawRightString(181 * mm, y, fmt(b_kt))
        y -= 7 * mm

        num = 0
        p.setFont(FONT, 9)

        def ensure_page_space(current_y: float) -> float:
            if current_y < 40 * mm:
                p.showPage()
                try:
                    p.setFont(BFONT, 10)
                except Exception:
                    p.setFont("Helvetica-Bold", 10)
                p.drawString(20 * mm, H - 20 * mm, "Продолжение акта сверки")
                yy = H - 30 * mm
                p.setFont(BFONT, 9)
                p.drawString(20 * mm, yy, "№")
                p.drawString(28 * mm, yy, "Содержание записи")
                p.drawString(100 * mm, yy, _safe(company_name))
                p.drawString(148 * mm, yy, _safe(client_name))
                yy -= 5 * mm
                p.drawString(100 * mm, yy, "Дт")
                p.drawString(118 * mm, yy, "Кт")
                p.drawString(148 * mm, yy, "Дт")
                p.drawString(166 * mm, yy, "Кт")
                p.line(20 * mm, yy - 1 * mm, 190 * mm, yy - 1 * mm)
                return yy - 6 * mm
            return current_y

        for row in entries:
            y = ensure_page_space(y)
            num += 1
            p.drawString(20 * mm, y, str(num))
            desc = row["title"]
            line1 = desc[:52]
            line2 = desc[52:104] if len(desc) > 52 else ""
            p.drawString(28 * mm, y, line1)

            p.drawRightString(115 * mm, y, fmt(row["a_debit"]))
            p.drawRightString(133 * mm, y, fmt(row["a_credit"]))
            p.drawRightString(163 * mm, y, fmt(row["b_debit"]))
            p.drawRightString(181 * mm, y, fmt(row["b_credit"]))
            y -= 6 * mm
            if line2:
                y = ensure_page_space(y)
                p.drawString(28 * mm, y, line2)
                y -= 6 * mm

        y -= 4 * mm
        p.line(20 * mm, y, 190 * mm, y)
        y -= 7 * mm
        p.setFont(BFONT, 10)
        p.drawString(28 * mm, y, "Итого обороты:")
        p.drawRightString(115 * mm, y, fmt(totals["a_debit"]))
        p.drawRightString(133 * mm, y, fmt(totals["a_credit"]))
        p.drawRightString(163 * mm, y, fmt(totals["b_debit"]))
        p.drawRightString(181 * mm, y, fmt(totals["b_credit"]))
        y -= 10 * mm

        debtor, creditor, amount = None, None, abs(closing)
        if closing > 0:
            debtor = client_name
            creditor = company_name
        elif closing < 0:
            debtor = company_name
            creditor = client_name

        p.setFont(FONT, 10)
        if amount == 0:
            phrase = f"Задолженность отсутствует на {on_date.strftime('%d.%m.%Y')}."
        else:
            phrase = (
                f"Задолженность {debtor} перед {creditor} на {on_date.strftime('%d.%m.%Y')} "
                f"составляет {fmt(amount)} {currency}"
            )
        p.drawString(20 * mm, y, phrase)
        y -= 8 * mm
        if amount == 0:
            p.drawString(20 * mm, y, "(Ноль сом 00 тыйын)")
        y -= 16 * mm

        p.setFont(BFONT, 10)
        p.drawString(20 * mm, y, _safe(company_name))
        p.drawString(110 * mm, y, _safe(client_name))
        y -= 8 * mm
        p.setFont(FONT, 10)
        p.drawString(20 * mm, y, "Главный бухгалтер: __________________")
        p.drawString(110 * mm, y, "Главный бухгалтер: __________________")

        p.showPage()
        p.save()
        buf.seek(0)
        filename = f"reconciliation_classic_{client.id}_{start_dt.date()}_{end_dt.date()}.pdf"
        return FileResponse(buf, as_attachment=True, filename=filename)

    def _error_pdf(self, message: str):
        buf = io.BytesIO()
        p = canvas.Canvas(buf, pagesize=A4)
        p.setFont("Helvetica-Bold", 14)
        p.drawString(30 * mm, 260 * mm, "Невозможно сформировать акт сверки")
        p.setFont("Helvetica", 11)
        p.drawString(30 * mm, 248 * mm, message)
        p.showPage()
        p.save()
        buf.seek(0)
        return FileResponse(buf, as_attachment=False, filename="reconciliation_error.pdf", status=400)


class SaleInvoiceDownloadAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        sale = get_object_or_404(
            Sale.objects.select_related("company", "user", "client").prefetch_related("items__product"),
            id=pk,
            company=request.user.company,
        )

        doc_no = ensure_sale_doc_number(sale)

        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=(210 * mm, 297 * mm))

        p.setFont("DejaVu-Bold", 14)
        p.drawCentredString(105 * mm, 280 * mm, f"НАКЛАДНАЯ № {doc_no}")
        p.setFont("DejaVu", 10)
        p.drawCentredString(105 * mm, 273 * mm, f"от {sale.created_at.strftime('%d.%m.%Y %H:%M')}")

        company = sale.company
        client = sale.client

        left = _party_lines(
            "КОМПАНИЯ",
            getattr(company, "llc", None) or getattr(company, "name", "—"),
            inn=getattr(company, "inn", None),
            okpo=getattr(company, "okpo", None),
            score=getattr(company, "score", None),
            bik=getattr(company, "bik", None),
            addr=getattr(company, "address", None),
            phone=getattr(company, "phone", None),
        )
        if client:
            right = _party_lines(
                "ПОКУПАТЕЛЬ",
                client.llc or client.enterprise or client.full_name,
                inn=client.inn,
                okpo=client.okpo,
                score=client.score,
                bik=client.bik,
                addr=client.address,
                phone=client.phone,
            )
        else:
            right = _party_lines("ПОКУПАТЕЛЬ", "—")

        y = 260 * mm
        x_left, x_right = 20 * mm, 110 * mm
        p.setFont("DejaVu-Bold", 10)
        p.drawString(x_left, y, left[0])
        p.drawString(x_right, y, right[0])
        y -= 6 * mm
        p.setFont("DejaVu", 10)
        for i in range(1, len(left)):
            p.drawString(x_left, y, left[i])
            p.drawString(x_right, y, right[i])
            y -= 6 * mm

        y -= 6 * mm
        p.setFont("DejaVu-Bold", 10)
        p.drawString(20 * mm, y, "Товар")
        p.drawRightString(140 * mm, y, "Кол-во")
        p.drawRightString(160 * mm, y, "Цена")
        p.drawRightString(190 * mm, y, "Сумма")

        y -= 5
        p.line(20 * mm, y, 190 * mm, y)
        y -= 10

        p.setFont("DejaVu", 10)
        for it in sale.items.all():
            p.drawString(20 * mm, y, (it.name_snapshot or "")[:60])
            p.drawRightString(140 * mm, y, str(it.quantity))
            p.drawRightString(160 * mm, y, fmt_money(it.unit_price))
            p.drawRightString(190 * mm, y, fmt_money(it.unit_price * it.quantity))
            y -= 7 * mm
            if y < 60 * mm:
                p.showPage()
                y = 270 * mm
                p.setFont("DejaVu", 10)

        y -= 10
        p.setFont("DejaVu-Bold", 11)
        p.drawRightString(190 * mm, y, f"СУММА (без скидок): {fmt_money(sale.subtotal)}")
        y -= 6 * mm
        if sale.discount_total and sale.discount_total > 0:
            p.drawRightString(190 * mm, y, f"СКИДКА: {fmt_money(sale.discount_total)}")
            y -= 6 * mm
        if sale.tax_total and sale.tax_total > 0:
            p.drawRightString(190 * mm, y, f"НАЛОГ: {fmt_money(sale.tax_total)}")
            y -= 6 * mm
        p.drawRightString(190 * mm, y, f"ИТОГО К ОПЛАТЕ: {fmt_money(sale.total)}")

        y -= 20
        p.setFont("DejaVu", 10)
        p.drawString(20 * mm, y, "Продавец: _____________")
        p.drawString(120 * mm, y, "Покупатель: _____________")

        p.showPage()
        p.save()

        buffer.seek(0)
        return FileResponse(buffer, as_attachment=True, filename=f"invoice_{doc_no}.pdf")


class SaleReceiptDataAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        sale = get_object_or_404(
            Sale.objects.select_related("company").prefetch_related("items"),
            id=pk,
            company=request.user.company,
        )
        cashier_name = (
            request.query_params.get("cashier_name")
            or getattr(request.user, "full_name", None)
            or getattr(request.user, "get_full_name", lambda: None)()
        )
        from apps.main.printers import build_receipt_payload

        payload = build_receipt_payload(sale, cashier_name=cashier_name, ensure_number=True)
        return Response(payload, status=200)

class SaleStartAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        user = request.user
        company = self._company() or user.company
        branch = self._auto_branch()

        cashbox_id = request.data.get("cashbox_id")
        opening_cash = request.data.get("opening_cash")

        cashbox = _resolve_pos_cashbox(company, branch, cashbox_id=cashbox_id)
        if not cashbox:
            raise ValidationError({"detail": "Нет кассы для этого филиала. Создай Cashbox."})

        shift = _ensure_open_shift(
            company=company,
            branch=branch,
            cashier=user,
            cashbox=cashbox,
            opening_cash=opening_cash,
        )

        qs = Cart.objects.filter(company=company, user=user, status=Cart.Status.ACTIVE, shift=shift).order_by("-created_at")
        cart = qs.first()

        if cart is None:
            cart = Cart.objects.create(company=company, user=user, status=Cart.Status.ACTIVE, branch=branch, shift=shift)
        else:
            extra_ids = list(qs.values_list("id", flat=True)[1:])
            if extra_ids:
                Cart.objects.filter(id__in=extra_ids).update(
                    status=Cart.Status.CHECKED_OUT,
                    updated_at=timezone.now(),
                )

        opts = StartCartOptionsSerializer(data=request.data)
        if opts.is_valid():
            order_disc = opts.validated_data.get("order_discount_total")
            if order_disc is not None:
                cart.order_discount_total = _q2(order_disc)
                cart.save(update_fields=["order_discount_total"])

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class CartDetailAPIView(generics.RetrieveAPIView):
    serializer_class = SaleCartSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Cart.objects.filter(company=self.request.user.company)

class SaleScanAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart,
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )

        ser = ScanRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        barcode = ser.validated_data["barcode"].strip()
        qty = ser.validated_data["quantity"]

        product = None
        scale_data = None

        # 1) обычный штрихкод
        try:
            product = Product.objects.get(company=cart.company, barcode=barcode)
        except Product.DoesNotExist:
            # 2) весовой штрихкод
            scale_data = _parse_scale_barcode(barcode)
            if not scale_data:
                return Response(
                    {"not_found": True, "message": "Товар не найден"},
                    status=404,
                )

            plu = scale_data["plu"]
            try:
                product = Product.objects.get(company=cart.company, plu=plu)
            except Product.DoesNotExist:
                return Response(
                    {
                        "not_found": True,
                        "message": f"Товар с ПЛУ {plu} не найден",
                    },
                    status=404,
                )

        # ✅ КОЛИЧЕСТВО
        if scale_data:
            effective_qty = Decimal(str(scale_data["weight_kg"]))
        else:
            effective_qty = Decimal(str(qty))

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={
                "company": cart.company,
                "branch": getattr(cart, "branch", None),
                "quantity": effective_qty,
                "unit_price": product.price,  # цена за кг или за штуку
            },
        )

        if not created:
            item.quantity = item.quantity + effective_qty
            item.save(update_fields=["quantity"])

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)



class SaleAddItemAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart,
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = AddItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        product = get_object_or_404(
            Product,
            id=ser.validated_data["product_id"],
            company=cart.company,
        )
        qty = ser.validated_data["quantity"]

        unit_price = ser.validated_data.get("unit_price")
        line_discount = ser.validated_data.get("discount_total")

        if unit_price is None:
            if line_discount is not None:
                per_unit_disc = _q2(Decimal(line_discount) / Decimal(qty))
                unit_price = _q2(Decimal(product.price) - per_unit_disc)
                if unit_price < 0:
                    unit_price = Decimal("0.00")
            else:
                unit_price = product.price

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={
                "company": cart.company,
                "branch": getattr(cart, "branch", None),
                "quantity": qty,
                "unit_price": unit_price,
            },
        )
        if not created:
            item.quantity += qty
            item.unit_price = unit_price
            item.save(update_fields=["quantity", "unit_price"])

        cart.recalc()

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)

class SaleCheckoutAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        qs = Cart.objects.filter(
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        if not _is_owner_like(request.user):
            qs = qs.filter(user=request.user)
        cart = get_object_or_404(qs)

        ser = CheckoutSerializer(data=request.data, context={"request": request, "cart": cart})
        ser.is_valid(raise_exception=True)

        print_receipt = ser.validated_data["print_receipt"]
        client_id = ser.validated_data.get("client_id")
        payment_method = ser.validated_data.get("payment_method") or Sale.PaymentMethod.CASH
        cash_received = ser.validated_data.get("cash_received") or Decimal("0.00")

        cashbox_id = ser.validated_data.get("cashbox_id")
        shift_id = ser.validated_data.get("shift_id")

        # 1) гарантируем shift на корзине
        if not cart.shift_id:
            company = cart.company
            branch = getattr(cart, "branch", None)

            cashbox = _resolve_pos_cashbox(company, branch, cashbox_id=cashbox_id)
            if not cashbox:
                raise ValidationError({"detail": "Нет кассы для этого филиала. Создай Cashbox."})

            if shift_id:
                shift = (
                    CashShift.objects
                    .select_for_update()
                    .filter(
                        id=shift_id,
                        company=company,
                        cashbox=cashbox,
                        status=CashShift.Status.OPEN,
                    )
                    .first()
                )
                if not shift:
                    raise ValidationError({"shift_id": "Смена не найдена или закрыта, или не относится к этой кассе."})

                if not _is_owner_like(request.user) and not getattr(request.user, "is_superuser", False):
                    if shift.cashier_id != request.user.id:
                        raise ValidationError({"shift_id": "Нельзя оформить продажу в чужую смену."})
            else:
                shift = _ensure_open_shift(
                    company=company,
                    branch=branch,
                    cashier=request.user,
                    cashbox=cashbox,
                )

            cart.shift = shift
            cart.save(update_fields=["shift"])

        # 2) пересчёт и проверка оплаты
        cart.recalc()
        if payment_method == Sale.PaymentMethod.CASH and cash_received < (cart.total or Decimal("0")):
            return Response(
                {"detail": "Сумма, полученная наличными, меньше суммы продажи."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3) checkout
        try:
            sale = checkout_cart(cart)
        except NotEnoughStock as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # 4) синхронизируем shift/cashbox
        if cart.shift_id:
            sh = CashShift.objects.select_related("cashbox").get(id=cart.shift_id)
            upd = []
            if sale.shift_id != sh.id:
                sale.shift_id = sh.id
                upd.append("shift")
            if getattr(sale, "cashbox_id", None) != sh.cashbox_id:
                sale.cashbox_id = sh.cashbox_id
                upd.append("cashbox")
            if upd:
                sale.save(update_fields=upd)

        # 5) client
        if client_id:
            client = get_object_or_404(Client, id=client_id, company=request.user.company)
            sale.client = client
            sale.save(update_fields=["client"])

        # 6) paid
        sale.mark_paid(payment_method=payment_method, cash_received=cash_received)

        # ✅ ВАЖНО: закрываем корзину и сбрасываем скидку, чтобы “следующая” не наследовала
        Cart.objects.filter(pk=cart.pk).update(
            status=Cart.Status.CHECKED_OUT,
            order_discount_total=Decimal("0.00"),
            updated_at=timezone.now(),
        )

        payload = {
            "sale_id": str(sale.id),
            "status": sale.status,
            "subtotal": fmt_money(sale.subtotal),
            "discount_total": fmt_money(sale.discount_total),
            "tax_total": fmt_money(sale.tax_total),
            "total": fmt_money(sale.total),
            "client": str(sale.client_id) if sale.client_id else None,
            "client_name": getattr(sale.client, "full_name", None) if sale.client else None,
            "payment_method": sale.payment_method,
            "cash_received": fmt_money(sale.cash_received),
            "change": fmt_money(sale.change),
            "shift_id": str(sale.shift_id) if sale.shift_id else None,
            "cashbox_id": str(sale.cashbox_id) if getattr(sale, "cashbox_id", None) else None,
        }

        if print_receipt:
            lines = [
                f"{(it.name_snapshot or '')[:40]} x{it.quantity} = {fmt_money((it.unit_price or 0) * (it.quantity or 0))}"
                for it in sale.items.all()
            ]
            totals = [f"СУММА: {fmt_money(sale.subtotal)}"]
            if sale.discount_total and sale.discount_total > 0:
                totals.append(f"СКИДКА: {fmt_money(sale.discount_total)}")
            if sale.tax_total and sale.tax_total > 0:
                totals.append(f"НАЛОГ: {fmt_money(sale.tax_total)}")
            totals.append(f"ИТОГО: {fmt_money(sale.total)}")

            if sale.payment_method == Sale.PaymentMethod.CASH:
                totals.append(f"ПОЛУЧЕНО НАЛИЧНЫМИ: {fmt_money(sale.cash_received)}")
                totals.append(f"СДАЧА: {fmt_money(sale.change)}")
            else:
                totals.append("ОПЛАТА ПЕРЕВОДОМ")

            payload["receipt_text"] = "ЧЕК\n" + "\n".join(lines) + "\n" + "\n".join(totals)

        return Response(payload, status=status.HTTP_201_CREATED)




class SaleMobileScannerTokenAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart,
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        token = MobileScannerToken.issue(cart, ttl_minutes=10)
        return Response(MobileScannerTokenSerializer(token).data, status=201)


class ProductFindByBarcodeAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        barcode = request.query_params.get("barcode", "").strip()
        if not barcode:
            return Response([], status=200)
        qs = Product.objects.filter(company=request.user.company, barcode=barcode)[:1]
        return Response(
            [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "barcode": p.barcode,
                    "price": str(p.price),
                }
                for p in qs
            ],
            status=200,
        )


class MobileScannerIngestAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    @transaction.atomic
    def post(self, request, token, *args, **kwargs):
        barcode = request.data.get("barcode", "").strip()
        qty = int(request.data.get("quantity", 1))
        if not barcode or qty <= 0:
            return Response({"detail": "barcode required"}, status=400)

        mt = MobileScannerToken.objects.select_related("cart", "cart__company").filter(token=token).first()
        if not mt:
            return Response({"detail": "invalid token"}, status=404)
        if not mt.is_valid():
            return Response({"detail": "token expired"}, status=410)

        cart = mt.cart
        try:
            product = Product.objects.get(company=cart.company, barcode=barcode)
        except Product.DoesNotExist:
            return Response({"not_found": True, "message": "Товар не найден"}, status=404)

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={
                "company": cart.company,
                "branch": getattr(cart, "branch", None),
                "quantity": qty,
                "unit_price": product.price,
            },
        )
        if not created:
            item.quantity += qty
            item.save(update_fields=["quantity"])
        cart.recalc()

        return Response({"ok": True}, status=201)


class SaleListAPIView(CompanyBranchRestrictedMixin, generics.ListAPIView):
    serializer_class = SaleListSerializer
    queryset = (
        Sale.objects
        .select_related("user")
        .prefetch_related("items__product")  # <-- важно для first_item_name
        .all()
    )
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ("status", "user")
    search_fields = ("id",)
    ordering_fields = ("created_at", "total", "status")
    ordering = ("-created_at",)

    def get_queryset(self):
        qs = super().get_queryset()

        start = self.request.query_params.get("start")
        end = self.request.query_params.get("end")
        if start:
            dt = parse_datetime(start) or (parse_date(start) and f"{start} 00:00:00")
            qs = qs.filter(created_at__gte=dt)
        if end:
            dt = parse_datetime(end) or (parse_date(end) and f"{end} 23:59:59")
            qs = qs.filter(created_at__lte=dt)

        paid_only = self.request.query_params.get("paid")
        if paid_only in ("1", "true", "True"):
            qs = qs.filter(status=Sale.Status.PAID)

        return qs


class SaleRetrieveAPIView(CompanyBranchRestrictedMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"
    lookup_url_kwarg = "pk"

    queryset = (
        Sale.objects.select_related("user")
        .prefetch_related("items__product")
        .all()
    )

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return SaleStatusUpdateSerializer
        return SaleDetailSerializer


class SaleBulkDeleteAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        ids = request.data.get("ids")
        allow_paid = bool(request.data.get("allow_paid", False))
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "Укажите непустой список 'ids'."}, status=400)

        valid_ids, invalid_ids = [], []
        for x in ids:
            try:
                valid_ids.append(uuid.UUID(str(x)))
            except Exception:
                invalid_ids.append(str(x))

        if not valid_ids and invalid_ids:
            return Response({"detail": "Нет валидных UUID.", "invalid_ids": invalid_ids}, status=400)

        base_qs = Sale.objects.filter(id__in=valid_ids)
        base_qs = self._filter_qs_company_branch(base_qs)

        if allow_paid:
            deletable_qs = base_qs
            not_allowed_ids = []
        else:
            deletable_qs = base_qs.exclude(status=Sale.Status.PAID)
            not_allowed_ids = list(
                base_qs.filter(status=Sale.Status.PAID).values_list("id", flat=True)
            )

        found_ids = set(str(sid) for sid in base_qs.values_list("id", flat=True))
        not_found_ids = [str(x) for x in valid_ids if str(x) not in found_ids]

        deleted_count, _ = deletable_qs.delete()

        return Response(
            {
                "deleted": deleted_count,
                "not_found": not_found_ids + invalid_ids,
                "not_allowed": [str(x) for x in not_allowed_ids],
            },
            status=200,
        )


class CartItemUpdateDestroyAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_active_cart(self, request, cart_id):
        return get_object_or_404(
            Cart,
            id=cart_id,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )

    def _get_item_in_cart(self, cart, item_or_product_id):
        item = CartItem.objects.filter(cart=cart, id=item_or_product_id).first()
        if item:
            return item

        item = CartItem.objects.filter(cart=cart, product_id=item_or_product_id).first()
        if item:
            return item

        raise Http404("CartItem not found in this cart.")

    @transaction.atomic
    def patch(self, request, cart_id, item_id, *args, **kwargs):
        cart = self._get_active_cart(request, cart_id)
        item = self._get_item_in_cart(cart, item_id)

        try:
            qty = int(request.data.get("quantity"))
        except (TypeError, ValueError):
            return Response({"quantity": "Укажите целое число >= 0."}, status=400)

        if qty < 0:
            return Response({"quantity": "Количество не может быть отрицательным."}, status=400)

        if qty == 0:
            item.delete()
            cart.recalc()
            return Response(SaleCartSerializer(cart).data, status=200)

        item.quantity = qty
        item.save(update_fields=["quantity"])
        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=200)

    @transaction.atomic
    def delete(self, request, cart_id, item_id, *args, **kwargs):
        cart = self._get_active_cart(request, cart_id)
        item = self._get_item_in_cart(cart, item_id)
        item.delete()
        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=200)


class SaleAddCustomItemAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart,
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = CustomCartItemCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        name = ser.validated_data["name"].strip()
        if not name:
            return Response({"name": "Название не может быть пустым."}, status=400)

        price = _q2(ser.validated_data["price"])
        qty = ser.validated_data.get("quantity", 1)

        item = CartItem.objects.filter(
            cart=cart,
            product__isnull=True,
            custom_name=name,
            unit_price=price,
        ).first()

        if item:
            CartItem.objects.filter(pk=item.pk).update(quantity=F("quantity") + qty)
            item.refresh_from_db(fields=["quantity"])
        else:
            CartItem.objects.create(
                company=cart.company,
                branch=getattr(cart, "branch", None),
                cart=cart,
                product=None,
                custom_name=name,
                unit_price=price,
                quantity=qty,
            )

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


OWNER_ROLES = {Roles.OWNER}


def _is_owner(user) -> bool:
    if getattr(user, "role", None) in OWNER_ROLES:
        company = getattr(user, "company", None)
        if company and getattr(company, "owner_id", None):
            return company.owner_id == user.id
        return True
    return False


def _agent_available_qty(user, company, product_id) -> int:
    sub = ManufactureSubreal.objects.filter(company=company, agent_id=user.id, product_id=product_id)
    accepted = sub.aggregate(s=Sum("qty_accepted"))["s"] or 0
    returned = sub.aggregate(s=Sum("qty_returned"))["s"] or 0
    sold = (
        AgentSaleAllocation.objects.filter(
            company=company,
            agent=user,
            product_id=product_id,
        ).aggregate(s=Sum("qty"))["s"]
        or 0
    )
    return int(accepted) - int(returned) - int(sold)


def _resolve_acting_agent(request, cart, *, allow_owner_override=True):
    user = request.user
    company = user.company

    agent_id = request.data.get("agent") or request.query_params.get("agent")
    if allow_owner_override and agent_id:
        if not _is_owner(user):
            raise ValidationError({"agent": "Только владелец может продавать за агента."})
        agent = get_object_or_404(User, id=agent_id, company=company)
        cache.set(f"cart_agent:{cart.id}", str(agent.id), timeout=60 * 60)
        return agent

    cached_id = cache.get(f"cart_agent:{cart.id}")
    if cached_id:
        try:
            return User.objects.get(id=cached_id, company=company)
        except User.DoesNotExist:
            cache.delete(f"cart_agent:{cart.id}")

    return user


@transaction.atomic
def _allocate_agent_sale(*, company, agent, sale: Sale):
    items = sale.items.select_related("product").all()

    for item in items:
        if not item.product_id:
            continue

        qty_to_allocate = int(item.quantity or 0)
        if qty_to_allocate <= 0:
            continue

        locked_subreals = list(
            ManufactureSubreal.objects.select_for_update()
            .filter(
                company=company,
                agent_id=agent.id,
                product_id=item.product_id,
            )
            .order_by("created_at", "id")
        )

        if not locked_subreals:
            raise ValidationError(
                {
                    "detail": f"У агента нет передач по товару "
                    f"{getattr(item.product, 'name', item.product_id)}."
                }
            )

        sub_ids = [s.id for s in locked_subreals]
        sold_map = {
            row["subreal_id"]: (row["s"] or 0)
            for row in AgentSaleAllocation.objects.filter(
                company=company,
                subreal_id__in=sub_ids,
            )
            .values("subreal_id")
            .annotate(s=Sum("qty"))
        }

        total_available = 0
        avail_rows = []
        for s in locked_subreals:
            sold = int(sold_map.get(s.id, 0))
            acc = int(s.qty_accepted or 0)
            ret = int(s.qty_returned or 0)
            avail = max(acc - ret - sold, 0)
            total_available += avail
            avail_rows.append((s, avail))

        if qty_to_allocate > total_available:
            name = getattr(item.product, "name", item.product_id)
            raise ValidationError(
                {
                    "detail": f"Недостаточно на руках у агента для товара {name}. "
                    f"Нужно {qty_to_allocate}, доступно {total_available}."
                }
            )

        for s, avail in avail_rows:
            if qty_to_allocate <= 0 or avail <= 0:
                continue
            take = min(avail, qty_to_allocate)

            AgentSaleAllocation.objects.create(
                company=company,
                agent=agent,
                subreal=s,
                sale=sale,
                sale_item=item,
                product=item.product,
                qty=take,
            )
            qty_to_allocate -= take

        if qty_to_allocate > 0:
            raise ValidationError({"detail": "Внутренняя ошибка распределения остатков по передачам."})


class AgentCartStartAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        user = request.user

        company = self._company() or user.company
        branch = self._auto_branch()

        qs = Cart.objects.filter(company=company, user=user, status=Cart.Status.ACTIVE)

        if hasattr(Cart, "branch"):
            if branch is not None:
                qs = qs.filter(branch=branch)
            else:
                qs = qs.filter(branch__isnull=True)

        qs = qs.order_by("-created_at")
        cart = qs.first()

        if cart is None:
            create_kwargs = dict(company=company, user=user, status=Cart.Status.ACTIVE)
            if hasattr(Cart, "branch"):
                create_kwargs["branch"] = branch
            cart = Cart.objects.create(**create_kwargs)
        else:
            extra_ids = list(qs.values_list("id", flat=True)[1:])
            if extra_ids:
                Cart.objects.filter(id__in=extra_ids).update(
                    status=Cart.Status.CHECKED_OUT,
                )

        _ = _resolve_acting_agent(request, cart, allow_owner_override=True)

        opts = StartCartOptionsSerializer(data=request.data)
        if opts.is_valid():
            order_disc = opts.validated_data.get("order_discount_total")
            if order_disc is not None:
                cart.order_discount_total = money(order_disc)
                cart.save(update_fields=["order_discount_total"])

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class AgentSaleScanAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart,
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = ScanRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        barcode = ser.validated_data["barcode"].strip()
        qty = ser.validated_data["quantity"]

        product = Product.objects.filter(company=cart.company, barcode=barcode).first()
        if not product:
            return Response({"not_found": True, "message": "Товар не найден"}, status=404)

        acting_agent = _resolve_acting_agent(request, cart, allow_owner_override=True)

        available = _agent_available_qty(acting_agent, cart.company, product.id)
        in_cart = (
            CartItem.objects.filter(cart=cart, product=product).aggregate(s=Sum("quantity"))["s"]
            or 0
        )
        if qty + in_cart > available:
            return Response(
                {
                    "detail": f"Недостаточно у агента. "
                    f"Доступно: {max(0, available - in_cart)}."
                },
                status=400,
            )

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={
                "company": cart.company,
                "branch": getattr(cart, "branch", None),
                "quantity": qty,
                "unit_price": product.price,
            },
        )
        if not created:
            item.quantity += qty
            item.save(update_fields=["quantity"])

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class AgentSaleAddItemAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart,
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = AddItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        product = get_object_or_404(
            Product,
            id=ser.validated_data["product_id"],
            company=cart.company,
        )
        qty = ser.validated_data["quantity"]

        acting_agent = _resolve_acting_agent(request, cart, allow_owner_override=True)

        available = _agent_available_qty(acting_agent, cart.company, product.id)
        in_cart = (
            CartItem.objects.filter(cart=cart, product=product).aggregate(s=Sum("quantity"))["s"]
            or 0
        )
        if qty + in_cart > available:
            return Response(
                {
                    "detail": f"Недостаточно у агента. "
                    f"Доступно: {max(0, available - in_cart)}."
                },
                status=400,
            )

        unit_price = ser.validated_data.get("unit_price")
        line_discount = ser.validated_data.get("discount_total")
        if unit_price is None:
            if line_discount is not None:
                per_unit_disc = money(Decimal(line_discount) / Decimal(qty))
                unit_price = money(Decimal(product.price) - per_unit_disc)
                if unit_price < 0:
                    unit_price = Decimal("0.00")
            else:
                unit_price = product.price

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={
                "company": cart.company,
                "branch": getattr(cart, "branch", None),
                "quantity": qty,
                "unit_price": unit_price,
            },
        )
        if not created:
            item.quantity += qty
            item.unit_price = unit_price
            item.save(update_fields=["quantity", "unit_price"])

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class AgentSaleAddCustomItemAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart,
            id=pk,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )
        ser = CustomCartItemCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        name = ser.validated_data["name"].strip()
        if not name:
            return Response({"name": "Название не может быть пустым."}, status=400)

        price = money(ser.validated_data["price"])
        qty = ser.validated_data.get("quantity", 1)

        item = CartItem.objects.filter(
            cart=cart,
            product__isnull=True,
            custom_name=name,
            unit_price=price,
        ).first()

        if item:
            CartItem.objects.filter(pk=item.pk).update(quantity=F("quantity") + qty)
            item.refresh_from_db(fields=["quantity"])
        else:
            CartItem.objects.create(
                company=cart.company,
                branch=getattr(cart, "branch", None),
                cart=cart,
                product=None,
                custom_name=name,
                unit_price=price,
                quantity=qty,
            )

        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)

class AgentSaleCheckoutAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        company = self._company() or request.user.company
        branch = self._auto_branch()

        cart_qs = Cart.objects.filter(
            id=pk,
            company=company,
            status=Cart.Status.ACTIVE,
        )
        if hasattr(Cart, "branch"):
            if branch is not None:
                cart_qs = cart_qs.filter(branch=branch)
            else:
                cart_qs = cart_qs.filter(branch__isnull=True)

        cart = get_object_or_404(cart_qs)

        # ✅ КРИТИЧНО: контекст обязателен (cart + request)
        ser = CheckoutSerializer(
            data=request.data,
            context={"request": request, "cart": cart},
        )
        ser.is_valid(raise_exception=True)

        print_receipt = ser.validated_data["print_receipt"]
        client_id = ser.validated_data.get("client_id")
        payment_method = ser.validated_data.get("payment_method") or Sale.PaymentMethod.CASH
        cash_received = ser.validated_data.get("cash_received") or Decimal("0.00")
        cashbox_id = ser.validated_data.get("cashbox_id")  # ✅ теперь используется

        if client_id:
            client = get_object_or_404(Client, id=client_id, company=company)
            if hasattr(cart, "client_id"):
                cart.client = client
                cart.save(update_fields=["client"])

        acting_agent = _resolve_acting_agent(request, cart, allow_owner_override=True)

        cart.recalc()
        if payment_method == Sale.PaymentMethod.CASH and cash_received < cart.total:
            raise ValidationError({"detail": "Сумма, полученная наличными, меньше суммы продажи."})

        try:
            # ✅ передаём кассу/оплату в сервис
            sale = checkout_agent_cart(
                cart,
                agent=acting_agent,
                cashbox_id=cashbox_id,
                payment_method=payment_method,
                cash_received=cash_received,
            )
        except Exception as e:
            raise ValidationError({"detail": str(e)})

        # ✅ если твой сервис не выставил paid_at/paid — добьём безопасно
        if hasattr(sale, "mark_paid") and callable(sale.mark_paid):
            # mark_paid уже мог быть вызван внутри — повторно норм, но если не хочешь — можешь проверять paid_at/status
            if sale.status != Sale.Status.PAID:
                sale.mark_paid(payment_method=payment_method, cash_received=cash_received)
        else:
            updates = []
            if hasattr(sale, "payment_method"):
                sale.payment_method = payment_method
                updates.append("payment_method")
            if hasattr(sale, "cash_received"):
                sale.cash_received = cash_received
                updates.append("cash_received")
            if hasattr(sale, "paid_at") and not sale.paid_at:
                sale.paid_at = timezone.now()
                updates.append("paid_at")
            if hasattr(sale, "status") and sale.status != Sale.Status.PAID:
                sale.status = Sale.Status.PAID
                updates.append("status")
            if updates:
                sale.save(update_fields=updates)

        payload = {
            "sale_id": str(sale.id),
            "status": sale.status,
            "subtotal": f"{sale.subtotal:.2f}",
            "discount_total": f"{sale.discount_total:.2f}",
            "tax_total": f"{sale.tax_total:.2f}",
            "total": f"{sale.total:.2f}",
            "client": str(sale.client_id) if sale.client_id else None,
            "client_name": getattr(sale.client, "full_name", None) if sale.client else None,
            "payment_method": getattr(sale, "payment_method", payment_method),
            "cash_received": f"{getattr(sale, 'cash_received', cash_received):.2f}",
            "change": f"{sale.change:.2f}" if hasattr(sale, "change") else "0.00",
            "shift_id": str(sale.shift_id) if getattr(sale, "shift_id", None) else None,
            "cashbox_id": str(sale.cashbox_id) if getattr(sale, "cashbox_id", None) else None,
        }

        if print_receipt:
            lines = [
                f"{(it.name_snapshot or '')[:40]} x{it.quantity} = {(it.unit_price or 0) * (it.quantity or 0):.2f}"
                for it in sale.items.all()
            ]
            totals = [f"СУММА: {sale.subtotal:.2f}"]
            if sale.discount_total and sale.discount_total > 0:
                totals.append(f"СКИДКА: {sale.discount_total:.2f}")
            if sale.tax_total and sale.tax_total > 0:
                totals.append(f"НАЛОГ: {sale.tax_total:.2f}")
            totals.append(f"ИТОГО: {sale.total:.2f}")

            if getattr(sale, "payment_method", payment_method) == Sale.PaymentMethod.CASH:
                totals.append(f"ПОЛУЧЕНО НАЛИЧНЫМИ: {getattr(sale, 'cash_received', cash_received):.2f}")
                totals.append(f"СДАЧА: {sale.change:.2f}" if hasattr(sale, "change") else "СДАЧА: 0.00")
            else:
                totals.append("ОПЛАТА ПЕРЕВОДОМ")

            payload["receipt_text"] = "ЧЕК\n" + "\n".join(lines) + "\n" + "\n".join(totals)

        return Response(payload, status=status.HTTP_201_CREATED)

class AgentCartItemUpdateDestroyAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_active_cart(self, request, cart_id):
        return get_object_or_404(
            Cart,
            id=cart_id,
            company=request.user.company,
            user=request.user,
            status=Cart.Status.ACTIVE,
        )

    def _get_item_in_cart(self, cart, item_or_product_id):
        item = CartItem.objects.filter(cart=cart, id=item_or_product_id).first()
        if item:
            return item
        item = CartItem.objects.filter(cart=cart, product_id=item_or_product_id).first()
        if item:
            return item
        raise Http404("CartItem not found in this cart.")

    @transaction.atomic
    def patch(self, request, cart_id, item_id, *args, **kwargs):
        cart = self._get_active_cart(request, cart_id)
        item = self._get_item_in_cart(cart, item_id)

        try:
            qty = int(request.data.get("quantity"))
        except (TypeError, ValueError):
            return Response({"quantity": "Укажите целое число >= 0."}, status=400)

        if qty < 0:
            return Response({"quantity": "Количество не может быть отрицательным."}, status=400)

        if qty == 0:
            item.delete()
            cart.recalc()
            return Response(SaleCartSerializer(cart).data, status=200)

        item.quantity = qty
        item.save(update_fields=["quantity"])
        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=200)

    @transaction.atomic
    def delete(self, request, cart_id, item_id, *args, **kwargs):
        cart = self._get_active_cart(request, cart_id)
        item = self._get_item_in_cart(cart, item_id)
        item.delete()
        cart.recalc()
        return Response(SaleCartSerializer(cart).data, status=200)
