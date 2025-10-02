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
from decimal import Decimal, ROUND_HALF_UP
import qrcode
import io, os, uuid
from django.db.models import Q, F
from apps.users.models import Company
from apps.main.models import Cart, CartItem, Sale, Product, MobileScannerToken, Client
from .pos_serializers import (
    SaleCartSerializer, SaleItemSerializer,
    ScanRequestSerializer, AddItemSerializer,
    CheckoutSerializer, MobileScannerTokenSerializer,
    SaleListSerializer, SaleDetailSerializer, StartCartOptionsSerializer, CustomCartItemCreateSerializer
)
from apps.main.services import checkout_cart, NotEnoughStock
from apps.main.views import CompanyRestrictedMixin
from apps.construction.models import Department
from django.http import Http404
from django.utils.timezone import is_aware, make_aware, get_current_timezone
from datetime import datetime, date, time as dtime
from django.db.models import Sum
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from typing import Iterable, List, Optional
from reportlab.pdfbase.ttfonts import TTFont
# from __future__ import annotations
from dataclasses import dataclass

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

class ClientReconciliationDownloadAPIView(APIView):
    """
    GET /api/main/clients/<uuid:client_id>/reconciliation/?start=YYYY-MM-DD&end=YYYY-MM-DD&source=both|sales|deals&currency=KGS
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, client_id, *args, **kwargs):
        company = request.user.company
        client = get_object_or_404(Client, id=client_id, company=company)

        # --- параметры
        q_start = request.query_params.get("start")
        q_end = request.query_params.get("end")
        source = (request.query_params.get("source") or "both").lower()  # both|sales|deals
        currency = request.query_params.get("currency") or "KGS"

        if not q_start or not q_end:
            return self._error_pdf("Укажите параметры start и end (YYYY-MM-DD).")

        start_dt = parse_datetime(q_start) or parse_date(q_start)
        end_dt = parse_datetime(q_end) or parse_date(q_end)
        if not start_dt or not end_dt:
            return self._error_pdf("Неверный формат дат. Используйте YYYY-MM-DD или ISO datetime.")

        start_dt = _aware(start_dt, end=False)
        end_dt = _aware(end_dt, end=True)

        # --- входящие обороты до начала периода
        debit_before = Decimal("0.00")
        credit_before = Decimal("0.00")

        # продажи → дебет
        if source in ("both", "sales"):
            sales_before = (Sale.objects
                            .filter(company=company, client=client, created_at__lt=start_dt)
                            .aggregate(s=Sum("total"))["s"] or Decimal("0"))
            debit_before += sales_before

        # сделки (SALE/AMOUNT/DEBT) → дебет
        if ClientDeal and source in ("both", "deals"):
            deals_before = (ClientDeal.objects
                            .filter(company=company, client=client, created_at__lt=start_dt,
                                    kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT])
                            .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
            debit_before += deals_before

            # предоплаты как кредит до начала
            pre_before = (ClientDeal.objects
                          .filter(company=company, client=client, created_at__lt=start_dt)
                          .aggregate(s=Sum("prepayment"))["s"] or Decimal("0"))
            credit_before += pre_before

        # оплаты по рассрочке → кредит
        if DealInstallment and source in ("both", "deals"):
            inst_before = (DealInstallment.objects
                           .filter(deal__company=company, deal__client=client,
                                   paid_on__isnull=False, paid_on__lt=start_dt.date())
                           .aggregate(s=Sum("amount"))["s"] or Decimal("0"))
            credit_before += inst_before

        opening = money(debit_before - credit_before)

        # --- движения за период
        entries: List[Entry] = []

        # продажи
        if source in ("both", "sales"):
            sales_qs = (Sale.objects
                        .filter(company=company, client=client,
                                created_at__gte=start_dt, created_at__lte=end_dt)
                        .order_by("created_at"))
            for s in sales_qs:
                entries.append(Entry(
                    date=s.created_at,
                    desc=f"Продажа {s.id}",
                    debit=money(s.total or 0),
                    credit=Decimal("0.00"),
                ))

        # сделки (дебет) и их предоплаты (кредит)
        if ClientDeal and source in ("both", "deals"):
            deals_qs = (ClientDeal.objects
                        .filter(company=company, client=client,
                                created_at__gte=start_dt, created_at__lte=end_dt,
                                kind__in=[ClientDeal.Kind.SALE, ClientDeal.Kind.AMOUNT, ClientDeal.Kind.DEBT])
                        .order_by("created_at"))
            for d in deals_qs:
                entries.append(Entry(
                    date=d.created_at,
                    desc=f"Сделка: {d.title} ({d.get_kind_display()})",
                    debit=money(d.amount or 0),
                    credit=Decimal("0.00"),
                ))
            pre_qs = (ClientDeal.objects
                      .filter(company=company, client=client,
                              prepayment__gt=0,
                              created_at__gte=start_dt, created_at__lte=end_dt)
                      .order_by("created_at"))
            for d in pre_qs:
                entries.append(Entry(
                    date=d.created_at,
                    desc=f"Предоплата (сделка: {d.title})",
                    debit=Decimal("0.00"),
                    credit=money(d.prepayment or 0),
                ))

        # оплаты по графику (кредит)
        if DealInstallment and source in ("both", "deals"):
            inst_qs = (DealInstallment.objects
                       .filter(deal__company=company, deal__client=client,
                               paid_on__isnull=False,
                               paid_on__gte=start_dt.date(), paid_on__lte=end_dt.date())
                       .select_related("deal").order_by("paid_on", "number"))
            for inst in inst_qs:
                entries.append(Entry(
                    date=_aware(inst.paid_on, end=False),
                    desc=f"Оплата по рассрочке №{inst.number} (сделка: {inst.deal.title})",
                    debit=Decimal("0.00"),
                    credit=money(inst.amount or 0),
                ))

        entries.sort(key=lambda e: e.date)
        period_debit = money(sum(e.debit for e in entries))
        period_credit = money(sum(e.credit for e in entries))
        closing = money(opening + period_debit - period_credit)

        # --- PDF
        buf = io.BytesIO()
        p = canvas.Canvas(buf, pagesize=A4)
        width, height = A4

        # шрифты
        try:
            body, bold = "DejaVu", "DejaVu-Bold"
            p.setFont(bold, 14)
        except Exception:
            body, bold = "Helvetica", "Helvetica-Bold"
            p.setFont(bold, 14)

        # верхняя шапка
        p.drawCentredString(width / 2, height - 20 * mm, "АКТ СВЕРКИ ВЗАИМНЫХ РАСЧЁТОВ")
        p.setFont(body, 10)
        p.drawCentredString(width / 2, height - 27 * mm,
                            f"Период: {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}   Валюта: {currency}")

        # реквизиты сторон
        def party_lines(title, name, inn=None, okpo=None, score=None, bik=None, addr=None, phone=None, email=None):
            return [
                title,
                name,
                f"ИНН: {_safe(inn)}   ОКПО: {_safe(okpo)}",
                f"Р/с: {_safe(score)}   БИК: {_safe(bik)}",
                f"Адрес: {_safe(addr)}",
                f"Тел.: {_safe(phone)}   E-mail: {_safe(email)}",
            ]

        company_name = getattr(company, "llc", None) or getattr(company, "name", str(company))
        client_name = client.llc or client.enterprise or client.full_name

        left = party_lines(
            "КОМПАНИЯ", company_name,
            inn=getattr(company, "inn", None),
            okpo=getattr(company, "okpo", None),
            score=getattr(company, "score", None),
            bik=getattr(company, "bik", None),
            addr=getattr(company, "address", None),
            phone=getattr(company, "phone", None),
            email=getattr(company, "email", None),
        )
        right = party_lines(
            "КЛИЕНТ", client_name,
            inn=client.inn, okpo=client.okpo, score=client.score, bik=client.bik,
            addr=client.address, phone=client.phone, email=client.email,
        )

        x_l, x_r = 20 * mm, 110 * mm
        y = height - 40 * mm
        p.setFont(bold, 10); p.drawString(x_l, y, left[0]); p.drawString(x_r, y, right[0]); y -= 6 * mm
        p.setFont(body, 10)
        for i in range(1, len(left)):
            p.drawString(x_l, y, left[i])
            p.drawString(x_r, y, right[i])
            y -= 6 * mm

        # таблица
        y -= 4 * mm
        p.setFont(bold, 10)
        p.drawString(20 * mm, y, "Дата")
        p.drawString(45 * mm, y, "Описание")
        p.drawRightString(140 * mm, y, "Дебет")
        p.drawRightString(170 * mm, y, "Кредит")
        p.drawRightString(190 * mm, y, "Сальдо")
        p.line(20 * mm, y - 2 * mm, 190 * mm, y - 2 * mm)
        y -= 8 * mm

        # входящее
        p.setFont(body, 10)
        p.drawString(45 * mm, y, "Входящее сальдо")
        running = opening
        p.drawRightString(190 * mm, y, fmt(running))
        y -= 6 * mm

        # строка-отрез
        p.line(120 * mm, y, 190 * mm, y); y -= 8 * mm

        # строки
        def draw_row(dt: datetime, desc: str, debit: Decimal, credit: Decimal, running_total: Decimal, y_pos: float) -> float:
            # перенос описания на 2 строки макс
            max_len = 64
            desc = desc or ""
            first = desc[:max_len]
            second = desc[max_len:2*max_len] if len(desc) > max_len else ""
            p.drawString(20 * mm, y_pos, dt.strftime("%d.%m.%Y"))
            p.drawString(45 * mm, y_pos, first)
            if debit > 0:
                p.drawRightString(140 * mm, y_pos, fmt(debit))
            if credit > 0:
                p.drawRightString(170 * mm, y_pos, fmt(credit))
            p.drawRightString(190 * mm, y_pos, fmt(running_total))
            y_pos -= 6 * mm
            if second:
                p.drawString(45 * mm, y_pos, second)
                y_pos -= 6 * mm
            return y_pos

        for e in entries:
            # новая страница?
            if y < 40 * mm:
                p.showPage()
                p.setFont(bold, 10)
                p.drawString(20 * mm, height - 20 * mm, "Продолжение акта сверки")
                p.line(20 * mm, height - 22 * mm, 190 * mm, height - 22 * mm)
                y = height - 30 * mm
                p.drawString(20 * mm, y, "Дата")
                p.drawString(45 * mm, y, "Описание")
                p.drawRightString(140 * mm, y, "Дебет")
                p.drawRightString(170 * mm, y, "Кредит")
                p.drawRightString(190 * mm, y, "Сальдо")
                p.line(20 * mm, y - 2 * mm, 190 * mm, y - 2 * mm)
                y -= 8 * mm
                p.setFont(body, 10)
                p.drawString(45 * mm, y, "Сальдо на продолжение")
                p.drawRightString(190 * mm, y, fmt(running))
                y -= 6 * mm
                p.line(120 * mm, y, 190 * mm, y); y -= 8 * mm

            running = money(running + e.debit - e.credit)
            y = draw_row(e.date, e.desc, e.debit, e.credit, running, y)

        # итоги
        y -= 4 * mm
        p.line(120 * mm, y, 190 * mm, y); y -= 8 * mm
        p.setFont(bold, 11)
        p.drawRightString(140 * mm, y, fmt(period_debit))
        p.drawRightString(170 * mm, y, fmt(period_credit))
        p.drawRightString(190 * mm, y, fmt(running))

        # подписи
        y -= 18 * mm
        p.setFont(body, 10)
        p.drawString(20 * mm, y, "Со стороны компании: Директор _____________   Гл. бухгалтер _____________")
        p.drawString(20 * mm, y - 6 * mm, company_name)
        p.drawString(20 * mm, y - 18 * mm, "Со стороны клиента:  Директор _____________   Гл. бухгалтер _____________")
        p.drawString(20 * mm, y - 24 * mm, client_name)

        p.showPage()
        p.save()
        buf.seek(0)

        fname = f"reconciliation_{client.id}_{start_dt.date()}_{end_dt.date()}_{source}.pdf"
        return FileResponse(buf, as_attachment=True, filename=fname)

    # простой генератор PDF с сообщением об ошибке
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
# pos/views.py
class SaleInvoiceDownloadAPIView(APIView):
    """
    GET /api/main/pos/sales/<uuid:pk>/invoice/
    Скачивание PDF-накладной по продаже (А4).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        sale = get_object_or_404(
            Sale.objects.select_related("company", "user").prefetch_related("items__product"),
            id=pk, company=request.user.company
        )

        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=(210 * mm, 297 * mm))  # A4

        # === Заголовок ===
        p.setFont("DejaVu-Bold", 14)
        p.drawCentredString(105 * mm, 280 * mm, f"НАКЛАДНАЯ № {sale.id}")
        p.setFont("DejaVu", 10)
        p.drawCentredString(105 * mm, 273 * mm, f"от {sale.created_at.strftime('%d.%m.%Y %H:%M')}")

        # Продавец / покупатель
        y = 260 * mm
        p.setFont("DejaVu", 10)
        p.drawString(20 * mm, y, f"Продавец: {sale.company.name}")
        y -= 6 * mm
        client = getattr(sale, "client", None)
        if client:
            p.drawString(20 * mm, y, f"Покупатель: {client.full_name}")
            y -= 6 * mm

        # === Таблица товаров ===
        y -= 10
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
            p.drawString(20 * mm, y, (it.name_snapshot or "")[:40])
            p.drawRightString(140 * mm, y, str(it.quantity))
            p.drawRightString(160 * mm, y, fmt_money(it.unit_price))
            p.drawRightString(190 * mm, y, fmt_money(it.unit_price * it.quantity))
            y -= 7 * mm
            if y < 50 * mm:
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
        return FileResponse(buffer, as_attachment=True, filename=f"invoice_{sale.id}.pdf")


class SaleReceiptDownloadAPIView(APIView):
    """
    GET /api/main/pos/sales/<uuid:pk>/receipt/
    Скачивание PDF-чека в формате кассового аппарата
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, *args, **kwargs):
        sale = get_object_or_404(
            Sale.objects.select_related("company").prefetch_related("items"),
            id=pk, company=request.user.company
        )

        # === Расчёт высоты под чек ===
        base_height = 80   # шапка и отступы
        per_item = 15      # место на строку товара
        qr_block = 40      # блок под QR
        extra_lines = 1 + (1 if sale.discount_total and sale.discount_total > 0 else 0) \
                        + (1 if sale.tax_total and sale.tax_total > 0 else 0) + 1
        extra_height = 6 * extra_lines + 6
        page_height = (base_height + per_item * sale.items.count() + qr_block + extra_height) * mm
        page_width = 58 * mm

        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=(page_width, page_height))
        y = page_height - 10 * mm

        # === Заголовок ===
        p.setFont("DejaVu-Bold", 12)
        p.drawCentredString(page_width / 2, y, "ЧЕК ПРОДАЖИ")
        y -= 10 * mm

        p.setFont("DejaVu", 9)

        # Номер чека и дата
        p.drawString(5 * mm, y, f"Продажа № {sale.id}")
        y -= 5 * mm
        p.drawString(5 * mm, y, sale.created_at.strftime("%d.%m.%Y %H:%M"))
        y -= 7 * mm

        # Разделитель
        p.drawCentredString(page_width / 2, y, "-" * 32)
        y -= 6 * mm

        # === Товары ===
        for it in sale.items.all():
            name = (it.name_snapshot or "")[:22]
            qty_price = f"{it.quantity} x {fmt_money(it.unit_price)}"
            total = fmt_money(it.unit_price * it.quantity)

            p.drawString(5 * mm, y, name)
            y -= 4 * mm
            p.drawString(5 * mm, y, qty_price)
            p.drawRightString(page_width - 5 * mm, y, total)
            y -= 6 * mm

        # Разделитель
        p.drawCentredString(page_width / 2, y, "-" * 32)
        y -= 6 * mm

        # === ИТОГИ ===
        p.setFont("DejaVu", 9)
        p.drawRightString(page_width - 5 * mm, y, f"СУММА: {fmt_money(sale.subtotal)}")
        y -= 6 * mm
        if sale.discount_total and sale.discount_total > 0:
            p.drawRightString(page_width - 5 * mm, y, f"СКИДКА: {fmt_money(sale.discount_total)}")
            y -= 6 * mm
        if sale.tax_total and sale.tax_total > 0:
            p.drawRightString(page_width - 5 * mm, y, f"НАЛОГ: {fmt_money(sale.tax_total)}")
            y -= 6 * mm

        p.setFont("DejaVu-Bold", 11)
        p.drawRightString(page_width - 5 * mm, y, f"ИТОГО: {fmt_money(sale.total)}")
        y -= 12 * mm

        # === QR-код ===
        qr_img = qrcode.make(f"SALE={sale.id};SUM={fmt_money(sale.total)};DATE={sale.created_at.isoformat()}")
        qr_size = 25 * mm
        qr_img = qr_img.resize((int(qr_size), int(qr_size)))

        p.drawImage(
            ImageReader(qr_img),
            (page_width - qr_size) / 2,
            y - qr_size,
            qr_size,
            qr_size
        )
        y -= (qr_size + 10)

        # === Подвал ===
        p.setFont("DejaVu", 9)
        p.drawCentredString(page_width / 2, y, "СПАСИБО ЗА ПОКУПКУ!")

        p.showPage()
        p.save()

        buffer.seek(0)
        return FileResponse(buffer, as_attachment=True, filename=f"receipt_{sale.id}.pdf")
    
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
            defaults={"company": cart.company, "quantity": qty, "unit_price": product.price},
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
            defaults={"company": cart.company, "quantity": qty, "unit_price": unit_price},
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

        # Определяем отдел — берём из сериализатора
        department_id = ser.validated_data.get("department_id")
        department = None
        if department_id:
            department = get_object_or_404(Department, id=department_id, company=request.user.company)

        try:
            sale = checkout_cart(cart, department=department)
        except NotEnoughStock as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # --- сохраняем клиента ---
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
                f"{it.name_snapshot} x{it.quantity} = {fmt_money(it.unit_price * it.quantity)}"
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
            defaults={"company": cart.company, "quantity": qty, "unit_price": product.price},
        )
        if not created:
            item.quantity += qty
            item.save(update_fields=["quantity"])
        cart.recalc()

        return Response({"ok": True}, status=201)


class SaleListAPIView(CompanyRestrictedMixin, generics.ListAPIView):
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
    GET /api/main/pos/sales/<uuid:pk>/
    Детальная продажа с её позициями.
    """
    serializer_class = SaleDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return get_object_or_404(
            Sale.objects.select_related("user").prefetch_related("items__product"),
            id=self.kwargs["pk"],
            company=self.request.user.company,  
        )

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
                cart=cart,
                product=None,
                custom_name=name,
                unit_price=price,
                quantity=qty,
            )

        # cart.recalc() не нужен: CartItem.save() уже пересчитает

        return Response(SaleCartSerializer(cart).data, status=status.HTTP_201_CREATED)