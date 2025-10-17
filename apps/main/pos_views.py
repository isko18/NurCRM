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
    SaleListSerializer, SaleDetailSerializer, StartCartOptionsSerializer, CustomCartItemCreateSerializer, SaleStatusUpdateSerializer
)
from apps.main.services import checkout_cart, NotEnoughStock
from apps.main.views import CompanyBranchRestrictedMixin
from apps.construction.models import Department
from django.http import Http404
from django.utils.timezone import is_aware, make_aware, get_current_timezone
from datetime import datetime, date, time as dtime
from django.db.models import Sum
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from typing import Iterable, List, Optional, Dict
from reportlab.pdfbase.ttfonts import TTFont
# from __future__ import annotations
from apps.main.models import ManufactureSubreal, AgentSaleAllocation
from apps.main.services_agent_pos import checkout_agent_cart, AgentNotEnoughStock
from dataclasses import dataclass
from apps.main.utils_numbers import ensure_sale_doc_number
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from apps.users.models import Roles, User, Company  # важно подтянуть Roles

try:
    from apps.main.models import ClientDeal, DealInstallment
except Exception:
    ClientDeal = None
    DealInstallment = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Путь к папке fonts внутри apps/main
FONTS_DIR = os.path.join(BASE_DIR, "fonts")

# Регистрируем шрифты
pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(FONTS_DIR, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf")))

# ---- money helpers ----
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
    # если в проекте уже зарегистрированы — ничего страшного
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

def _party_lines(title, name, inn=None, okpo=None, score=None, bik=None, addr=None, phone=None, email=None):
    return [
        title,
        name,
        f"ИНН: {_safe(inn)}   ОКПО: {_safe(okpo)}",
        f"Р/с: {_safe(score)}   БИК: {_safe(bik)}",
        f"Адрес: {_safe(addr)}",
        f"Тел.: {_safe(phone)}",
    ]

class ClientReconciliationClassicAPIView(APIView):
    """
    GET /api/main/clients/<uuid:client_id>/reconciliation/classic/?start=YYYY-MM-DD&end=YYYY-MM-DD&source=both|sales|deals&currency=KGS
    Отрисовка «классического» акта: №, Содержание, Дт/Кт компании и Дт/Кт клиента.
    Логика проводок: увеличение долга клиента (продажа/сумма сделки) -> Дт Компания / Кт Клиент.
                     оплата/предоплата -> Кт Компания / Дт Клиент.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, client_id, *args, **kwargs):
        company = request.user.company
        client = get_object_or_404(Client, id=client_id, company=company)

        source = (request.query_params.get("source") or "both").lower()  # both|sales|deals
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

        # ===== входящие обороты до периода
        debit_before = Decimal("0.00")   # увеличивали долг клиента
        credit_before = Decimal("0.00")  # погашали долг клиента

        # продажи -> дебет
        if source in ("both", "sales"):
            sales_before = (Sale.objects
                            .filter(company=company, client=client, created_at__lt=start_dt)
                            .aggregate(s=Sum("total"))["s"] or Decimal("0"))
            debit_before += sales_before

        if ClientDeal and source in ("both", "deals"):
            # сделки SALE/AMOUNT/DEBT -> дебет
            deals_before = (ClientDeal.objects
                            .filter(company=company, client=client, created_at__lt=start_dt,
                                    kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT])
                            .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
            debit_before += deals_before
            # предоплаты -> кредит
            pre_before = (ClientDeal.objects
                          .filter(company=company, client=client, created_at__lt=start_dt)
                          .aggregate(s=Sum("prepayment"))["s"] or Decimal("0"))
            credit_before += pre_before

        if DealInstallment and source in ("both", "deals"):
            inst_before = (DealInstallment.objects
                           .filter(deal__company=company, deal__client=client,
                                   paid_on__isnull=False, paid_on__lt=start_dt.date())
                           .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
            credit_before += inst_before

        opening = q2(debit_before - credit_before)  # >0 клиент должен компании; <0 наоборот

        # ===== движения периода: собираем «универсальные» элементы
        entries: List[Dict] = []

        # Продажа = рост долга -> Дт Компании / Кт Клиента
        if source in ("both", "sales"):
            for s in (Sale.objects
                      .filter(company=company, client=client, created_at__gte=start_dt, created_at__lte=end_dt)
                      .order_by("created_at")):
                if q2(s.total) > 0:
                    entries.append(dict(
                        date=s.created_at,
                        title=f"Продажа {s.id}",
                        a_debit=q2(s.total), a_credit=Decimal("0.00"),
                        b_debit=Decimal("0.00"), b_credit=q2(s.total),
                    ))

        if ClientDeal and source in ("both", "deals"):
            # Суммы сделок = рост долга
            for d in (ClientDeal.objects
                      .filter(company=company, client=client, created_at__gte=start_dt, created_at__lte=end_dt,
                              kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT])
                      .order_by("created_at")):
                amt = q2(d.amount)
                if amt > 0:
                    entries.append(dict(
                        date=d.created_at,
                        title=f"Сделка: {d.title} ({d.get_kind_display()})",
                        a_debit=amt, a_credit=Decimal("0.00"),
                        b_debit=Decimal("0.00"), b_credit=amt,
                    ))
            # Предоплата = погашение долга
            for d in (ClientDeal.objects
                      .filter(company=company, client=client, prepayment__gt=0,
                              created_at__gte=start_dt, created_at__lte=end_dt)
                      .order_by("created_at")):
                pp = q2(d.prepayment)
                entries.append(dict(
                    date=d.created_at,
                    title=f"Предоплата (сделка: {d.title})",
                    a_debit=Decimal("0.00"), a_credit=pp,
                    b_debit=pp, b_credit=Decimal("0.00"),
                ))

        # Платежи по графику = погашение долга
        if DealInstallment and source in ("both", "deals"):
            for inst in (DealInstallment.objects
                         .filter(deal__company=company, deal__client=client,
                                 paid_on__isnull=False, paid_on__gte=start_dt.date(), paid_on__lte=end_dt.date())
                         .select_related("deal").order_by("paid_on", "number")):
                amt = q2(inst.amount)
                dt = _aware(inst.paid_on, end=False)
                entries.append(dict(
                    date=dt,
                    title=f"Оплата по рассрочке №{inst.number} (сделка: {inst.deal.title})",
                    a_debit=Decimal("0.00"), a_credit=amt,
                    b_debit=amt, b_credit=Decimal("0.00"),
                ))

        entries.sort(key=lambda x: x["date"])

        # итоги оборотов по 4 колонкам
        totals = dict(
            a_debit=q2(sum(x["a_debit"] for x in entries) if entries else 0),
            a_credit=q2(sum(x["a_credit"] for x in entries) if entries else 0),
            b_debit=q2(sum(x["b_debit"] for x in entries) if entries else 0),
            b_credit=q2(sum(x["b_credit"] for x in entries) if entries else 0),
        )

        # конечное сальдо (как «одностороннее»)
        closing = q2(opening + totals["a_debit"] - totals["a_credit"])  # для стороны A; у B будет зеркально
        # текстовая дата «на …» — следующий день после конца периода, как в примере
        on_date = (end_dt + timedelta(days=1)).date()

        # ===== PDF
        buf = io.BytesIO()
        p = canvas.Canvas(buf, pagesize=A4)
        W, H = A4

        # шрифт
        try:
            FONT, BFONT = "DejaVu", "DejaVu-Bold"
            p.setFont(BFONT, 14)
        except Exception:
            FONT, BFONT = "Helvetica", "Helvetica-Bold"
            p.setFont(BFONT, 14)

        # заголовок
        p.drawCentredString(W/2, H - 20*mm, "АКТ СВЕРКИ ВЗАИМНЫХ РАСЧЁТОВ")
        p.setFont(FONT, 11)
        p.drawCentredString(W/2, H - 27*mm,
                            f"Период: {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}   валюта сверки {currency}")

        # шапка сторон (крупные названия)
        company_name = getattr(company, "llc", None) or getattr(company, "name", str(company))
        client_name = client.llc or client.enterprise or client.full_name

        p.setFont(BFONT, 10)
        p.drawString(20*mm, H - 38*mm, "КОМПАНИЯ")
        p.drawString(110*mm, H - 38*mm, "КЛИЕНТ")
        p.setFont(FONT, 11)
        p.drawString(20*mm, H - 44*mm, _safe(company_name))
        p.drawString(110*mm, H - 44*mm, _safe(client_name))
        p.setFont(FONT, 9)
        p.drawString(20*mm, H - 50*mm, f"ИНН: {_safe(getattr(company,'inn',None))}    ОКПО: {_safe(getattr(company,'okpo',None))}")
        p.drawString(110*mm, H - 50*mm, f"ИНН: {_safe(client.inn)}    ОКПО: {_safe(client.okpo)}")
        p.drawString(20*mm, H - 56*mm, f"Р/с: {_safe(getattr(company,'score',None))}    БИК: {_safe(getattr(company,'bik',None))}")
        p.drawString(110*mm, H - 56*mm, f"Р/с: {_safe(client.score)}    БИК: {_safe(client.bik)}")
        p.drawString(20*mm, H - 62*mm, f"Адрес: {_safe(getattr(company,'address',None))}")
        p.drawString(110*mm, H - 62*mm, f"Адрес: {_safe(client.address)}")
        p.drawString(20*mm, H - 68*mm, f"Тел.: {_safe(getattr(company,'phone',None))}    E-mail: {_safe(getattr(company,'email',None))}")
        p.drawString(110*mm, H - 68*mm, f"Тел.: {_safe(client.phone)}    E-mail: {_safe(client.email)}")

        # таблица: колонки как в образце
        y = H - 78*mm
        p.setFont(BFONT, 9)
        p.drawString(20*mm, y, "№")
        p.drawString(28*mm, y, "Содержание записи")
        p.drawString(100*mm, y, _safe(company_name))
        p.drawString(148*mm, y, _safe(client_name))

        y -= 5*mm
        p.setFont(BFONT, 9)
        p.drawString(100*mm, y, "Дт");  p.drawString(118*mm, y, "Кт")
        p.drawString(148*mm, y, "Дт");  p.drawString(166*mm, y, "Кт")
        p.line(20*mm, y-1*mm, 190*mm, y-1*mm)
        y -= 6*mm

        # строка «Сальдо начальное»
        p.setFont(FONT, 9)
        # расщепляем opening на 2 стороны
        a_dt = opening if opening > 0 else Decimal("0.00")
        a_kt = -opening if opening < 0 else Decimal("0.00")
        b_dt = a_kt
        b_kt = a_dt

        p.drawString(28*mm, y, "Сальдо начальное")
        p.drawRightString(115*mm, y, fmt(a_dt))
        p.drawRightString(133*mm, y, fmt(a_kt))
        p.drawRightString(163*mm, y, fmt(b_dt))
        p.drawRightString(181*mm, y, fmt(b_kt))
        y -= 7*mm

        # строки движений, нумерация
        num = 0
        p.setFont(FONT, 9)

        def ensure_page_space(current_y: float) -> float:
            if current_y < 40*mm:
                p.showPage()
                # повторить мини-шапку
                try:
                    p.setFont(BFONT, 10)
                except Exception:
                    p.setFont("Helvetica-Bold", 10)
                p.drawString(20*mm, H - 20*mm, "Продолжение акта сверки")
                yy = H - 30*mm
                p.setFont(BFONT, 9)
                p.drawString(20*mm, yy, "№")
                p.drawString(28*mm, yy, "Содержание записи")
                p.drawString(100*mm, yy, _safe(company_name))
                p.drawString(148*mm, yy, _safe(client_name))
                yy -= 5*mm
                p.drawString(100*mm, yy, "Дт");  p.drawString(118*mm, yy, "Кт")
                p.drawString(148*mm, yy, "Дт");  p.drawString(166*mm, yy, "Кт")
                p.line(20*mm, yy-1*mm, 190*mm, yy-1*mm)
                return yy - 6*mm
            return current_y

        for row in entries:
            y = ensure_page_space(y)
            num += 1
            p.drawString(20*mm, y, str(num))
            # контент (2 строки макс, как в примере)
            desc = row["title"]
            line1 = desc[:52]
            line2 = desc[52:104] if len(desc) > 52 else ""
            p.drawString(28*mm, y, line1)

            # суммы по сторонам уже «зеркальные»
            p.drawRightString(115*mm, y, fmt(row["a_debit"]))
            p.drawRightString(133*mm, y, fmt(row["a_credit"]))
            p.drawRightString(163*mm, y, fmt(row["b_debit"]))
            p.drawRightString(181*mm, y, fmt(row["b_credit"]))
            y -= 6*mm
            if line2:
                y = ensure_page_space(y)
                p.drawString(28*mm, y, line2)
                y -= 6*mm

        # итоги оборотов (как в образце — по 4 колонкам)
        y -= 4*mm
        p.line(20*mm, y, 190*mm, y); y -= 7*mm
        p.setFont(BFONT, 10)
        p.drawString(28*mm, y, "Итого обороты:")
        p.drawRightString(115*mm, y, fmt(totals["a_debit"]))
        p.drawRightString(133*mm, y, fmt(totals["a_credit"]))
        p.drawRightString(163*mm, y, fmt(totals["b_debit"]))
        p.drawRightString(181*mm, y, fmt(totals["b_credit"]))
        y -= 10*mm

        # конечное сальдо — выводим фразу
        # кто кому должен по итогам: если closing > 0, клиент должен компании; если < 0 — наоборот
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
            phrase = (f"Задолженность {debtor} перед {creditor} на {on_date.strftime('%d.%m.%Y')} "
                      f"составляет {fmt(amount)} {currency}")
        p.drawString(20*mm, y, phrase)
        y -= 8*mm
        if amount == 0:
            p.drawString(20*mm, y, "(Ноль сом 00 тыйын)")
        y -= 16*mm

        # подписи
        p.setFont(BFONT, 10)
        p.drawString(20*mm, y, _safe(company_name))
        p.drawString(110*mm, y, _safe(client_name))
        y -= 8*mm
        p.setFont(FONT, 10)
        p.drawString(20*mm, y, "Главный бухгалтер: __________________")
        p.drawString(110*mm, y, "Главный бухгалтер: __________________")

        p.showPage()
        p.save()
        buf.seek(0)
        filename = f"reconciliation_classic_{client.id}_{start_dt.date()}_{end_dt.date()}.pdf"
        return FileResponse(buf, as_attachment=True, filename=filename)

    def _error_pdf(self, message: str):
        buf = io.BytesIO()
        p = canvas.Canvas(buf, pagesize=A4)
        p.setFont("Helvetica-Bold", 14)
        p.drawString(30*mm, 260*mm, "Невозможно сформировать акт сверки")
        p.setFont("Helvetica", 11)
        p.drawString(30*mm, 248*mm, message)
        p.showPage()
        p.save()
        buf.seek(0)
        return FileResponse(buf, as_attachment=False, filename="reconciliation_error.pdf", status=400)

# pos/views.py
class SaleInvoiceDownloadAPIView(APIView):
    """
    GET /api/main/pos/sales/<uuid:pk>/invoice/
    Скачивание PDF-накладной по продаже (А4).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        sale = get_object_or_404(
            Sale.objects.select_related("company", "user", "client").prefetch_related("items__product"),
            id=pk, company=request.user.company
        )

        # присвоим сквозной номер, если ещё нет
        doc_no = ensure_sale_doc_number(sale)

        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=(210 * mm, 297 * mm))  # A4

        # === Заголовок ===
        p.setFont("DejaVu-Bold", 14)
        p.drawCentredString(105 * mm, 280 * mm, f"НАКЛАДНАЯ № {doc_no}")
        p.setFont("DejaVu", 10)
        p.drawCentredString(105 * mm, 273 * mm, f"от {sale.created_at.strftime('%d.%m.%Y %H:%M')}")

        # === Реквизиты сторон (две колонки) ===
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
            # email=getattr(company, "email", None),
        )
        if client:
            right = _party_lines(
                "ПОКУПАТЕЛЬ",
                client.llc or client.enterprise or client.full_name,
                inn=client.inn, okpo=client.okpo,
                score=client.score, bik=client.bik,
                addr=client.address, phone=client.phone
            )
        else:
            right = _party_lines("ПОКУПАТЕЛЬ", "—")

        y = 260 * mm
        x_left, x_right = 20 * mm, 110 * mm
        p.setFont("DejaVu-Bold", 10); p.drawString(x_left, y, left[0]); p.drawString(x_right, y, right[0]); y -= 6 * mm
        p.setFont("DejaVu", 10)
        for i in range(1, len(left)):
            p.drawString(x_left, y, left[i])
            p.drawString(x_right, y, right[i])
            y -= 6 * mm

        # === Таблица товаров ===
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

        # === ИТОГ ===
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

        # === Подписи ===
        y -= 20
        p.setFont("DejaVu", 10)
        p.drawString(20 * mm, y, "Продавец: _____________")
        p.drawString(120 * mm, y, "Покупатель: _____________")

        p.showPage()
        p.save()

        buffer.seek(0)
        return FileResponse(buffer, as_attachment=True, filename=f"invoice_{doc_no}.pdf")


class SaleReceiptDownloadAPIView(APIView):
    """
    GET /api/main/pos/sales/<uuid:pk>/receipt/
    Печать чека сразу в USB-принтер на этой машине (касовый ПК).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        sale = get_object_or_404(
            Sale.objects.select_related("company").prefetch_related("items"),
            id=pk, company=request.user.company
        )
        doc_no = ensure_sale_doc_number(sale)

        try:
            from .printers import UsbEscposPrinter
            UsbEscposPrinter().print_sale(sale, doc_no, fmt_money)
            return Response({"ok": True, "printed": True, "doc_no": doc_no}, status=200)
        except Exception as e:
            return Response({"ok": False, "printed": False, "doc_no": doc_no, "error": str(e)}, status=500)

class SaleStartAPIView(APIView):
    """
    POST — создать/получить активную корзину для текущего пользователя.
    Если найдено несколько активных — оставим самую свежую, остальные закроем.
    + Опционально: принять скидку на итог (сумма) при старте.
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        user = request.user
        company = user.company

        qs = (Cart.objects
              .filter(company=company, user=user, status=Cart.Status.ACTIVE)
              .order_by('-created_at'))
        cart = qs.first()
        if cart is None:
            cart = Cart.objects.create(company=company, user=user, status=Cart.Status.ACTIVE)
        else:
            # закрыть дубликаты, если есть
            extra_ids = list(qs.values_list('id', flat=True)[1:])
            if extra_ids:
                Cart.objects.filter(id__in=extra_ids).update(
                    status=Cart.Status.CHECKED_OUT, updated_at=timezone.now()
                )

        # === НОВОЕ: суммовая скидка на весь заказ ===
        opts = StartCartOptionsSerializer(data=request.data)
        # мягкая валидация: если фронт ничего не прислал — не ломаем контракт
        if opts.is_valid():
            order_disc = opts.validated_data.get("order_discount_total")
            if order_disc is not None:
                # нормализуем до 2 знаков и сохраняем
                cart.order_discount_total = _q2(order_disc)
                cart.save(update_fields=["order_discount_total"])

        # пересчёт итогов с учётом order_discount_total
        cart.recalc()

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)



class CartDetailAPIView(generics.RetrieveAPIView):
    """
    GET /api/main/pos/carts/<uuid:pk>/ — получить корзину по id.
    """
    serializer_class = SaleCartSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Cart.objects.filter(company=self.request.user.company)


class SaleScanAPIView(APIView):
    """
    POST — добавить товар по штрих-коду (сканер ПК).
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE
        )
        ser = ScanRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        barcode = ser.validated_data["barcode"].strip()
        qty = ser.validated_data["quantity"]

        try:
            product = Product.objects.get(company=cart.company, barcode=barcode)
        except Product.DoesNotExist:
            return Response({"not_found": True, "message": "Товар не найден"}, status=404)

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={
                "company": cart.company,
                "branch": getattr(cart, "branch", None),  # ← NEW: подставляем branch
                "quantity": qty,
                "unit_price": product.price,
            },
        )
        if not created:
            item.quantity += qty
            item.save(update_fields=["quantity"])
        cart.recalc()

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class SaleAddItemAPIView(APIView):
    """
    POST — ручное добавление товара после поиска.
    Можно передать либо unit_price, либо discount_total (на всю строку).
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE
        )
        ser = AddItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        product = get_object_or_404(
            Product, id=ser.validated_data["product_id"], company=cart.company
        )
        qty = ser.validated_data["quantity"]

        # --- Цена/скидка на строку ---
        unit_price = ser.validated_data.get("unit_price")
        line_discount = ser.validated_data.get("discount_total")

        if unit_price is None:
            if line_discount is not None:
                # скидка на всю строку -> рассчитать цену за ед.
                per_unit_disc = _q2(Decimal(line_discount) / Decimal(qty))
                unit_price = _q2(Decimal(product.price) - per_unit_disc)
                if unit_price < 0:
                    unit_price = Decimal("0.00")
            else:
                unit_price = product.price  # без скидки

        item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={
                "company": cart.company,
                "branch": getattr(cart, "branch", None),  # ← NEW
                "quantity": qty,
                "unit_price": unit_price,
            },
        )
        if not created:
            # объединяем строки: увеличиваем количество и обновляем цену на позицию
            item.quantity += qty
            item.unit_price = unit_price
            item.save(update_fields=["quantity", "unit_price"])

        cart.recalc()

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)

class SaleCheckoutAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE
        )

        ser = CheckoutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        print_receipt = ser.validated_data["print_receipt"]
        client_id = ser.validated_data.get("client_id")

        department = None
        department_id = ser.validated_data.get("department_id")
        if department_id:
            department = get_object_or_404(Department, id=department_id, company=request.user.company)

        try:
            sale = checkout_cart(cart, department=department)
        except NotEnoughStock as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # ← NEW: синхронизируем branch продажи с branch корзины
        if hasattr(sale, "branch_id") and hasattr(cart, "branch_id"):
            if sale.branch_id != cart.branch_id:
                sale.branch_id = cart.branch_id
                sale.save(update_fields=["branch"])
        # ← NEW: на всякий случай обновим branch у позиций продажи, если null
        try:
            sale.items.filter(branch__isnull=True).update(branch=getattr(cart, "branch", None))
        except Exception:
            pass

        if client_id:
            client = get_object_or_404(Client, id=client_id, company=request.user.company)
            sale.client = client
            sale.save(update_fields=["client"])

        payload = {
            "sale_id": str(sale.id),
            "status": sale.status,
            "subtotal": fmt_money(sale.subtotal),
            "discount_total": fmt_money(sale.discount_total),
            "tax_total": fmt_money(sale.tax_total),
            "total": fmt_money(sale.total),
            "client": str(sale.client_id) if sale.client_id else None,
            "client_name": getattr(sale.client, "full_name", None) if sale.client else None,
        }

        if print_receipt:
            lines = [
                f"{(it.name_snapshot or '')[:40]} x{it.quantity} = {fmt_money((it.unit_price or 0)* (it.quantity or 0))}"
                for it in sale.items.all()
            ]
            totals = [f"СУММА: {fmt_money(sale.subtotal)}"]
            if sale.discount_total and sale.discount_total > 0:
                totals.append(f"СКИДКА: {fmt_money(sale.discount_total)}")
            if sale.tax_total and sale.tax_total > 0:
                totals.append(f"НАЛОГ: {fmt_money(sale.tax_total)}")
            totals.append(f"ИТОГО: {fmt_money(sale.total)}")
            payload["receipt_text"] = "ЧЕК\n" + "\n".join(lines) + "\n" + "\n".join(totals)

        return Response(payload, status=status.HTTP_201_CREATED)


class SaleMobileScannerTokenAPIView(APIView):
    """
    POST — выдать токен для телефона как сканера.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE
        )
        token = MobileScannerToken.issue(cart, ttl_minutes=10)
        return Response(MobileScannerTokenSerializer(token).data, status=201)


class ProductFindByBarcodeAPIView(APIView):
    """
    GET — поиск товара по штрих-коду (строгий, 0 или 1 запись).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        barcode = request.query_params.get("barcode", "").strip()
        if not barcode:
            return Response([], status=200)
        qs = Product.objects.filter(company=request.user.company, barcode=barcode)[:1]
        return Response([
            {"id": str(p.id), "name": p.name, "barcode": p.barcode, "price": str(p.price)}
            for p in qs
        ], status=200)


class MobileScannerIngestAPIView(APIView):
    """
    POST — телефон отправляет штрих-код в корзину по токену.
    """
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
                "branch": getattr(cart, "branch", None),  # ← NEW
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
    """
    GET /api/main/pos/sales/?status=&start=&end=&user=
    Возвращает список продаж по компании с простыми фильтрами.
    """
    serializer_class = SaleListSerializer
    queryset = Sale.objects.select_related("user").all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ("status", "user")
    search_fields = ("id",)
    ordering_fields = ("created_at", "total", "status")
    ordering = ("-created_at",)

    def get_queryset(self):
        qs = super().get_queryset().filter(company=self.request.user.company)

        # Фильтры по дате (поддерживаются YYYY-MM-DD и ISO datetime)
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


class SaleRetrieveAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/main/pos/sales/<uuid:pk>/  — детальная продажа с позициями
    PATCH  /api/main/pos/sales/<uuid:pk>/  — обновить статус
    PUT    /api/main/pos/sales/<uuid:pk>/  — обновить статус
    DELETE /api/main/pos/sales/<uuid:pk>/  — удалить
    """
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "id"
    lookup_url_kwarg = "pk"

    # read-сериализатор с детальными полями (status — единственное writable)
    serializer_class = SaleDetailSerializer

    def get_queryset(self):
        # базовый queryset с нужными join/prefetch и ограничением по компании
        return (
            Sale.objects
            .select_related("user")
            .prefetch_related("items__product")
            .filter(company=self.request.user.company)
        )

    def get_serializer_class(self):
        # На запись — узкий сериализатор только для статуса
        if self.request.method in ("PUT", "PATCH"):
            return SaleStatusUpdateSerializer
        return SaleDetailSerializer

class SaleBulkDeleteAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        ids = request.data.get("ids")
        allow_paid = bool(request.data.get("allow_paid", False))
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "Укажите непустой список 'ids'."}, status=400)

        # валидируем UUID (чтобы не падать на мусорных значениях)
        valid_ids, invalid_ids = [], []
        for x in ids:
            try:
                valid_ids.append(uuid.UUID(str(x)))
            except Exception:
                invalid_ids.append(str(x))

        if not valid_ids and invalid_ids:
            return Response({"detail": "Нет валидных UUID.", "invalid_ids": invalid_ids}, status=400)

        # продажи только своей компании
        base_qs = Sale.objects.filter(company=request.user.company, id__in=valid_ids)

        # делим на разрешённые к удалению и запрещённые (например, оплаченные)
        if allow_paid:
            deletable_qs = base_qs
            not_allowed_ids = []
        else:
            deletable_qs = base_qs.exclude(status=Sale.Status.PAID)
            not_allowed_ids = list(
                base_qs.filter(status=Sale.Status.PAID).values_list("id", flat=True)
            )

        # найдены в принципе?
        found_ids = set(str(sid) for sid in base_qs.values_list("id", flat=True))
        not_found_ids = [str(x) for x in valid_ids if str(x) not in found_ids]

        # удаляем (с каскадом по items)
        deleted_count, _ = deletable_qs.delete()

        return Response(
            {
                "deleted": deleted_count,     # количество удалённых объектов (включая каскад может быть > продаж)
                "not_found": not_found_ids + invalid_ids,
                "not_allowed": [str(x) for x in not_allowed_ids],
            },
            status=200,
        )

class CartItemUpdateDestroyAPIView(APIView):
    """
    PATCH /api/main/pos/carts/<uuid:cart_id>/items/<uuid:item_id>/
      body: {"quantity": <int >= 0>}
      quantity == 0 -> удалить позицию
      quantity > 0  -> установить новое количество

    DELETE /api/main/pos/carts/<uuid:cart_id>/items/<uuid:item_id>/
      удалить позицию

    Примечание:
      item_id может быть как ID позиции корзины, так и ID товара.
    """
    permission_classes = [permissions.IsAuthenticated]

    def _get_active_cart(self, request, cart_id):
        return get_object_or_404(
            Cart,
            id=cart_id,
            company=request.user.company,
            status=Cart.Status.ACTIVE,
        )

    def _get_item_in_cart(self, cart, item_or_product_id):
        # 1) пробуем как ID позиции корзины
        item = CartItem.objects.filter(cart=cart, id=item_or_product_id).first()
        if item:
            return item

        # 2) пробуем как ID товара (некоторые фронты шлют product_id)
        item = CartItem.objects.filter(cart=cart, product_id=item_or_product_id).first()
        if item:
            return item

        raise Http404("CartItem not found in this cart.")

    @transaction.atomic
    def patch(self, request, cart_id, item_id, *args, **kwargs):
        cart = self._get_active_cart(request, cart_id)
        item = self._get_item_in_cart(cart, item_id)

        # Валидация количества
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
    """
    POST /api/main/pos/carts/<uuid:pk>/custom-item/
    body: {"name": "Диагностика", "price": "500.00", "quantity": 1}
    Добавляет кастомную позицию (без product) в корзину.
    """
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(
            Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE
        )
        ser = CustomCartItemCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        name = ser.validated_data["name"].strip()
        if not name:
            return Response({"name": "Название не может быть пустым."}, status=400)

        price = _q2(ser.validated_data["price"])   # нормализуем деньги
        qty = ser.validated_data.get("quantity", 1)

        # (опция) объединять одинаковые кастомные позиции
        item = CartItem.objects.filter(
            cart=cart, product__isnull=True, custom_name=name, unit_price=price
        ).first()

        if item:
            CartItem.objects.filter(pk=item.pk).update(quantity=F("quantity") + qty)
            item.refresh_from_db(fields=["quantity"])
        else:
            CartItem.objects.create(
                company=cart.company,
                branch=getattr(cart, "branch", None),  # ← NEW: сохраняем branch у кастомной позиции
                cart=cart,
                product=None,
                custom_name=name,
                unit_price=price,
                quantity=qty,
            )

        # cart.recalc() не нужен: CartItem.save() уже пересчитает

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)

OWNER_ROLES = {Roles.OWNER}  # можно расширить по желанию

def _is_owner(user) -> bool:
    """
    Является ли пользователь владельцем компании:
    - системная роль == owner
    И (по желанию, но полезно):
    - он действительно указан как owner у своей Company
    """
    if getattr(user, "role", None) in OWNER_ROLES:
        company = getattr(user, "company", None)
        if company and getattr(company, "owner_id", None):
            return company.owner_id == user.id
        return True
    return False


# ---------------------------
# Остатки агента (простой вариант)
# ---------------------------

def _agent_available_qty(user, company, product_id) -> int:
    """
    Сколько у агента на руках: accepted - returned - sold(allocations)
    """
    sub = ManufactureSubreal.objects.filter(company=company, agent_id=user.id, product_id=product_id)
    accepted = sub.aggregate(s=Sum("qty_accepted"))["s"] or 0
    returned = sub.aggregate(s=Sum("qty_returned"))["s"] or 0
    sold = (
        AgentSaleAllocation.objects
        .filter(company=company, agent=user, product_id=product_id)
        .aggregate(s=Sum("qty"))["s"] or 0
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


# ---------------------------
# Аллокатор (исправленный: без N+1 и рассинхрона)
# ---------------------------

@transaction.atomic
def _allocate_agent_sale(*, company, agent, sale: Sale):
    """
    FIFO-распределение продаж по передачам агента.
    Избегаем 'FOR UPDATE ... GROUP BY': лочим subreals отдельно,
    агрегации делаем во втором запросе без блокировок.
    """
    items = sale.items.select_related("product").all()

    for item in items:
        if not item.product_id:
            continue  # кастомные позиции не списывают остатки

        qty_to_allocate = int(item.quantity or 0)
        if qty_to_allocate <= 0:
            continue

        # 1) Лочим только передачи без агрегатов (нет GROUP BY)
        locked_subreals = list(
            ManufactureSubreal.objects
            .select_for_update()
            .filter(company=company, agent_id=agent.id, product_id=item.product_id)
            .order_by("created_at", "id")  # FIFO
        )

        if not locked_subreals:
            raise ValidationError({
                "detail": f"У агента нет передач по товару {getattr(item.product, 'name', item.product_id)}."
            })

        sub_ids = [s.id for s in locked_subreals]

        # 2) Подтягиваем агрегаты одним запросом (уже без FOR UPDATE)
        sold_map = {
            row["subreal_id"]: (row["s"] or 0)
            for row in AgentSaleAllocation.objects
                .filter(company=company, subreal_id__in=sub_ids)
                .values("subreal_id")
                .annotate(s=Sum("qty"))
        }

        # 3) Считаем общий доступный
        total_available = 0
        avail_rows = []
        for s in locked_subreals:
            sold = int(sold_map.get(s.id, 0))
            acc  = int(s.qty_accepted or 0)
            ret  = int(s.qty_returned or 0)
            avail = max(acc - ret - sold, 0)
            total_available += avail
            avail_rows.append((s, avail))

        if qty_to_allocate > total_available:
            name = getattr(item.product, "name", item.product_id)
            raise ValidationError({
                "detail": f"Недостаточно на руках у агента для товара {name}. "
                          f"Нужно {qty_to_allocate}, доступно {total_available}."
            })

        # 4) Выделяем по FIFO и создаём allocations
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
            # Не должно случиться после общей проверки
            raise ValidationError({"detail": "Внутренняя ошибка распределения остатков по передачам."})

# ===========================
# ВЬЮХИ: корзина/скан/добавление/кастом/чекаут
# ===========================

class AgentCartStartAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        user = request.user
        company = user.company

        qs = (
            Cart.objects
            .filter(company=company, user=user, status=Cart.Status.ACTIVE)
            .order_by("-created_at")
        )
        cart = qs.first()
        if cart is None:
            cart = Cart.objects.create(company=company, user=user, status=Cart.Status.ACTIVE)
        else:
            extra_ids = list(qs.values_list("id", flat=True)[1:])
            if extra_ids:
                # при желании можно использовать CLOSED/ABANDONED
                Cart.objects.filter(id__in=extra_ids).update(status=Cart.Status.CHECKED_OUT)

        # зафиксируем агента (если owner передал)
        _ = _resolve_acting_agent(request, cart, allow_owner_override=True)

        # опциональная суммовая скидка
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
        cart = get_object_or_404(Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE)
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
            CartItem.objects.filter(cart=cart, product=product)
            .aggregate(s=Sum("quantity"))["s"] or 0
        )
        if qty + in_cart > available:
            return Response({"detail": f"Недостаточно у агента. Доступно: {max(0, available - in_cart)}."}, status=400)

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
        cart = get_object_or_404(Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE)
        ser = AddItemSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        product = get_object_or_404(Product, id=ser.validated_data["product_id"], company=cart.company)
        qty = ser.validated_data["quantity"]

        acting_agent = _resolve_acting_agent(request, cart, allow_owner_override=True)

        available = _agent_available_qty(acting_agent, cart.company, product.id)
        in_cart = (
            CartItem.objects.filter(cart=cart, product=product)
            .aggregate(s=Sum("quantity"))["s"] or 0
        )
        if qty + in_cart > available:
            return Response({"detail": f"Недостаточно у агента. Доступно: {max(0, available - in_cart)}."}, status=400)

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
        cart = get_object_or_404(Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE)
        ser = CustomCartItemCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        name = ser.validated_data["name"].strip()
        if not name:
            return Response({"name": "Название не может быть пустым."}, status=400)

        price = money(ser.validated_data["price"])
        qty = ser.validated_data.get("quantity", 1)

        item = CartItem.objects.filter(
            cart=cart, product__isnull=True, custom_name=name, unit_price=price
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

        cart.recalc()  # ← добавили пересчёт перед ответом
        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)


class AgentSaleCheckoutAPIView(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, *args, **kwargs):
        cart = get_object_or_404(Cart, id=pk, company=request.user.company, status=Cart.Status.ACTIVE)

        ser = CheckoutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        print_receipt = ser.validated_data["print_receipt"]
        client_id = ser.validated_data.get("client_id")

        department = None
        department_id = ser.validated_data.get("department_id")
        if department_id:
            department = get_object_or_404(Department, id=department_id, company=request.user.company)

        if client_id:
            client = get_object_or_404(Client, id=client_id, company=request.user.company)
            if hasattr(cart, "client_id"):
                cart.client = client
                cart.save(update_fields=["client"])

        # кто списывает остатки — агент из cache/параметра или текущий пользователь
        acting_agent = _resolve_acting_agent(request, cart, allow_owner_override=True)

        # ---- создаём продажу (если твой checkout уже принимает agent — передай) ----
        try:
            sale = checkout_agent_cart(cart, department=department, agent=acting_agent)
        except Exception as e:
            # важно: не возвращаем Response внутри транзакции — кидаем ValidationError
            transaction.set_rollback(True)
            raise ValidationError({"detail": str(e)})

        # привести branch продажи к branch корзины, если нужно
        if hasattr(sale, "branch_id") and hasattr(cart, "branch_id") and sale.branch_id != cart.branch_id:
            sale.branch_id = cart.branch_id
            sale.save(update_fields=["branch"])

        # ---- распределяем продажу по передачам агента ----
        _allocate_agent_sale(company=cart.company, agent=acting_agent, sale=sale)

        payload = {
            "sale_id": str(sale.id),
            "status": sale.status,
            "subtotal": f"{sale.subtotal:.2f}",
            "discount_total": f"{sale.discount_total:.2f}",
            "tax_total": f"{sale.tax_total:.2f}",
            "total": f"{sale.total:.2f}",
            "client": str(sale.client_id) if sale.client_id else None,
            "client_name": getattr(sale.client, "full_name", None) if sale.client else None,
        }

        if print_receipt:
            lines = [
                f"{(it.name_snapshot or '')[:40]} x{it.quantity} = { (it.unit_price or 0) * (it.quantity or 0):.2f}"
                for it in sale.items.all()
            ]
            totals = [f"СУММА: {sale.subtotal:.2f}"]
            if sale.discount_total and sale.discount_total > 0:
                totals.append(f"СКИДКА: {sale.discount_total:.2f}")
            if sale.tax_total and sale.tax_total > 0:
                totals.append(f"НАЛОГ: {sale.tax_total:.2f}")
            totals.append(f"ИТОГО: {sale.total:.2f}")
            payload["receipt_text"] = "ЧЕК\n" + "\n".join(lines) + "\n" + "\n".join(totals)

        return Response(payload, status=status.HTTP_201_CREATED)


class AgentCartItemUpdateDestroyAPIView(APIView):
    """
    PATCH /api/main/agents/me/carts/<uuid:cart_id>/items/<uuid:item_id>/
      body: {"quantity": <int >= 0>}
      quantity == 0 -> удалить позицию
      quantity > 0  -> установить новое количество

    DELETE /api/main/agents/me/carts/<uuid:cart_id>/items/<uuid:item_id>/
      удалить позицию

    Примечание:
      item_id может быть как ID позиции корзины, так и ID товара.
    """
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
        # 1) как ID позиции корзины
        item = CartItem.objects.filter(cart=cart, id=item_or_product_id).first()
        if item:
            return item
        # 2) как ID товара
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