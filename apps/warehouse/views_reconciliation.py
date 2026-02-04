import io
import os
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date, time as dtime, timedelta

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime, parse_date
from django.utils import timezone
from django.utils.timezone import is_aware, make_aware, get_current_timezone
from django.db.models import Sum

from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from . import models
from .views import CompanyBranchRestrictedMixin


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(os.path.dirname(BASE_DIR), "main", "fonts")

try:
    pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(FONTS_DIR, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf")))
except Exception:
    # если шрифтов нет в окружении — PDF всё равно сгенерится на Helvetica
    pass


DOC_DEBIT = {
    models.Document.DocType.SALE,
    models.Document.DocType.PURCHASE_RETURN,
}
DOC_CREDIT = {
    models.Document.DocType.PURCHASE,
    models.Document.DocType.SALE_RETURN,
}
DOC_TYPES = DOC_DEBIT | DOC_CREDIT

MONEY_DEBIT = {models.MoneyDocument.DocType.MONEY_EXPENSE}
MONEY_CREDIT = {models.MoneyDocument.DocType.MONEY_RECEIPT}


def _set_font(p, name: str, size: int, fallback: str = "Helvetica"):
    try:
        p.setFont(name, size)
    except Exception:
        p.setFont(fallback, size)


def _aware(dt_or_date, end=False):
    tz = get_current_timezone()
    if isinstance(dt_or_date, datetime):
        return dt_or_date if is_aware(dt_or_date) else make_aware(dt_or_date, tz)
    if isinstance(dt_or_date, date):
        t = dtime(23, 59, 59) if end else dtime(0, 0, 0)
        return make_aware(datetime.combine(dt_or_date, t), tz)
    return None


def _safe(v) -> str:
    return v if (v is not None and str(v).strip()) else "—"


def q2(x: Decimal) -> Decimal:
    return (x or Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt(x: Decimal) -> str:
    return f"{q2(x):.2f}"


def _as_decimal(v, default=Decimal("0.00")) -> Decimal:
    try:
        return Decimal(str(v).replace(",", "."))
    except Exception:
        return default


class _CounterpartyReconciliationBase(CompanyBranchRestrictedMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _parse_range(self, request):
        s = request.query_params.get("start")
        e = request.query_params.get("end")
        if not s or not e:
            return None, None, "Укажите параметры start и end (YYYY-MM-DD)."

        start_raw = parse_datetime(s) or parse_date(s)
        end_raw = parse_datetime(e) or parse_date(e)
        if not start_raw or not end_raw:
            return None, None, "Неверный формат дат. Используйте YYYY-MM-DD или ISO datetime."

        start_dt = _aware(start_raw, end=False)
        end_dt = _aware(end_raw, end=True)
        if not start_dt or not end_dt:
            return None, None, "Неверный формат дат. Используйте YYYY-MM-DD или ISO datetime."

        if start_dt > end_dt:
            return None, None, "start не может быть больше end."

        return start_dt, end_dt, None

    def _company_for_display(self):
        user = getattr(self, "request", None).user
        return getattr(user, "owned_company", None) or getattr(user, "company", None)

    def _filter_docs_qs(self, qs):
        company = self._company()
        branch = self._auto_branch()

        if company is not None:
            qs = qs.filter(warehouse_from__company=company)

        if branch is not None:
            qs = qs.filter(warehouse_from__branch=branch)
        elif company is not None:
            qs = qs.filter(warehouse_from__branch__isnull=True)

        return qs

    def _filter_money_qs(self, qs):
        company = self._company()
        branch = self._auto_branch()

        if company is not None:
            qs = qs.filter(company=company)

        if branch is not None:
            qs = qs.filter(branch=branch)
        elif company is not None:
            qs = qs.filter(branch__isnull=True)

        return qs

    def _get_qs(self, counterparty):
        docs_qs = (
            models.Document.objects.filter(
                counterparty=counterparty,
                status=models.Document.Status.POSTED,
                doc_type__in=DOC_TYPES,
            )
            .select_related("warehouse_from", "counterparty")
        )
        money_qs = (
            models.MoneyDocument.objects.filter(
                counterparty=counterparty,
                status=models.MoneyDocument.Status.POSTED,
                doc_type__in=(tuple(MONEY_DEBIT | MONEY_CREDIT)),
            )
            .select_related("warehouse", "counterparty")
        )

        return self._filter_docs_qs(docs_qs), self._filter_money_qs(money_qs)

    def _sum_amount(self, qs, field_name: str) -> Decimal:
        val = qs.aggregate(s=Sum(field_name)).get("s")
        return q2(val or Decimal("0.00"))

    def _opening_balance(self, docs_qs, money_qs, start_dt):
        debit_before = self._sum_amount(
            docs_qs.filter(doc_type__in=DOC_DEBIT, date__lt=start_dt),
            "total",
        )
        credit_before = self._sum_amount(
            docs_qs.filter(doc_type__in=DOC_CREDIT, date__lt=start_dt),
            "total",
        )

        debit_before += self._sum_amount(
            money_qs.filter(doc_type__in=MONEY_DEBIT, date__lt=start_dt),
            "amount",
        )
        credit_before += self._sum_amount(
            money_qs.filter(doc_type__in=MONEY_CREDIT, date__lt=start_dt),
            "amount",
        )

        opening = q2(debit_before - credit_before)
        return opening, debit_before, credit_before

    def _build_entries(self, docs_qs, money_qs, start_dt, end_dt):
        entries = []

        docs = docs_qs.filter(date__gte=start_dt, date__lte=end_dt).order_by("date")
        for doc in docs:
            amt = q2(doc.total or Decimal("0.00"))
            if amt <= 0:
                continue
            is_debit = doc.doc_type in DOC_DEBIT
            a_debit = amt if is_debit else Decimal("0.00")
            a_credit = Decimal("0.00") if is_debit else amt
            entries.append(
                {
                    "date": doc.date,
                    "title": f"{doc.get_doc_type_display()} {doc.number or doc.id}",
                    "a_debit": a_debit,
                    "a_credit": a_credit,
                    "b_debit": a_credit,
                    "b_credit": a_debit,
                    "ref_type": f"document:{doc.doc_type}",
                    "ref_id": str(doc.id),
                }
            )

        money_docs = money_qs.filter(date__gte=start_dt, date__lte=end_dt).order_by("date")
        for doc in money_docs:
            amt = q2(doc.amount or Decimal("0.00"))
            if amt <= 0:
                continue
            is_debit = doc.doc_type in MONEY_DEBIT
            a_debit = amt if is_debit else Decimal("0.00")
            a_credit = Decimal("0.00") if is_debit else amt
            entries.append(
                {
                    "date": doc.date,
                    "title": f"{doc.get_doc_type_display()} {doc.number or doc.id}",
                    "a_debit": a_debit,
                    "a_credit": a_credit,
                    "b_debit": a_credit,
                    "b_credit": a_debit,
                    "ref_type": f"money:{doc.doc_type}",
                    "ref_id": str(doc.id),
                }
            )

        entries.sort(key=lambda x: x["date"])
        return entries

    def _totals_from_entries(self, entries):
        totals = {"a_debit": Decimal("0.00"), "a_credit": Decimal("0.00"), "b_debit": Decimal("0.00"), "b_credit": Decimal("0.00")}
        for r in entries:
            totals["a_debit"] += _as_decimal(r.get("a_debit"), Decimal("0.00"))
            totals["a_credit"] += _as_decimal(r.get("a_credit"), Decimal("0.00"))
            totals["b_debit"] += _as_decimal(r.get("b_debit"), Decimal("0.00"))
            totals["b_credit"] += _as_decimal(r.get("b_credit"), Decimal("0.00"))
        totals = {k: q2(v) for k, v in totals.items()}
        return totals


class CounterpartyReconciliationJSONAPIView(_CounterpartyReconciliationBase):
    def get(self, request, counterparty_id, *args, **kwargs):
        counterparty = get_object_or_404(models.Counterparty, id=counterparty_id)
        company = self._company_for_display()
        currency = request.query_params.get("currency") or "KGS"

        start_dt, end_dt, error = self._parse_range(request)
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        docs_qs, money_qs = self._get_qs(counterparty)
        opening, _deb_before, _cred_before = self._opening_balance(docs_qs, money_qs, start_dt)
        entries = self._build_entries(docs_qs, money_qs, start_dt, end_dt)

        totals = self._totals_from_entries(entries)
        closing = q2(opening + totals["a_debit"] - totals["a_credit"])
        as_of_date = (end_dt + timedelta(days=1)).date()

        company_name = getattr(company, "llc", None) or getattr(company, "name", None) or str(company) if company else "—"
        counterparty_name = getattr(counterparty, "name", None) or "—"

        debtor = None
        creditor = None
        amount = abs(closing)
        if closing > 0:
            debtor = counterparty_name
            creditor = company_name
        elif closing < 0:
            debtor = company_name
            creditor = counterparty_name

        running = opening
        entries_out = []
        for r in entries:
            a_deb = q2(r["a_debit"])
            a_cre = q2(r["a_credit"])
            running = q2(running + a_deb - a_cre)
            dt = r["date"]
            dt_local = timezone.localtime(dt) if hasattr(timezone, "localtime") else dt
            entries_out.append(
                {
                    "date": dt_local.isoformat(),
                    "title": r["title"],
                    "a_debit": fmt(a_deb),
                    "a_credit": fmt(a_cre),
                    "b_debit": fmt(q2(r["b_debit"])),
                    "b_credit": fmt(q2(r["b_credit"])),
                    "ref_type": r.get("ref_type"),
                    "ref_id": r.get("ref_id"),
                    "running_balance_after": fmt(running),
                }
            )

        payload = {
            "company": {
                "id": str(getattr(company, "id", "")) if company else None,
                "name": company_name,
                "inn": _safe(getattr(company, "inn", None)) if company else "—",
                "okpo": _safe(getattr(company, "okpo", None)) if company else "—",
                "score": _safe(getattr(company, "score", None)) if company else "—",
                "bik": _safe(getattr(company, "bik", None)) if company else "—",
                "address": _safe(getattr(company, "address", None)) if company else "—",
                "phone": _safe(getattr(company, "phone", None)) if company else "—",
                "email": _safe(getattr(company, "email", None)) if company else "—",
            },
            "counterparty": {
                "id": str(counterparty.id),
                "name": counterparty_name,
                "type": counterparty.type,
            },
            "period": {
                "start": start_dt.date().isoformat(),
                "end": end_dt.date().isoformat(),
                "currency": currency,
            },
            "opening_balance": fmt(opening),
            "entries": entries_out,
            "totals": {k: fmt(v) for k, v in totals.items()},
            "closing_balance": fmt(closing),
            "as_of_date": as_of_date.isoformat(),
            "debt": {
                "debtor": debtor,
                "creditor": creditor,
                "amount": fmt(amount),
                "currency": currency,
            },
        }
        return Response(payload, status=200)


class CounterpartyReconciliationClassicAPIView(_CounterpartyReconciliationBase):
    def get(self, request, counterparty_id, *args, **kwargs):
        counterparty = get_object_or_404(models.Counterparty, id=counterparty_id)
        company = self._company_for_display()
        currency = request.query_params.get("currency") or "KGS"

        start_dt, end_dt, error = self._parse_range(request)
        if error:
            return self._error_pdf(error)

        docs_qs, money_qs = self._get_qs(counterparty)
        opening, _deb_before, _cred_before = self._opening_balance(docs_qs, money_qs, start_dt)
        entries = self._build_entries(docs_qs, money_qs, start_dt, end_dt)

        totals = self._totals_from_entries(entries)
        closing = q2(opening + totals["a_debit"] - totals["a_credit"])
        on_date = (end_dt + timedelta(days=1)).date()

        buf = io.BytesIO()
        p = canvas.Canvas(buf, pagesize=A4)
        W, H = A4

        _set_font(p, "DejaVu-Bold", 14, fallback="Helvetica-Bold")
        p.drawCentredString(W / 2, H - 20 * mm, "АКТ СВЕРКИ ВЗАИМНЫХ РАСЧЁТОВ")
        _set_font(p, "DejaVu", 11, fallback="Helvetica")
        p.drawCentredString(
            W / 2,
            H - 27 * mm,
            f"Период: {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}   валюта сверки {currency}",
        )

        company_name = getattr(company, "llc", None) or getattr(company, "name", None) or str(company) if company else "—"
        counterparty_name = getattr(counterparty, "name", None) or "—"

        _set_font(p, "DejaVu-Bold", 10, fallback="Helvetica-Bold")
        p.drawString(20 * mm, H - 38 * mm, "КОМПАНИЯ")
        p.drawString(110 * mm, H - 38 * mm, "КОНТРАГЕНТ")
        _set_font(p, "DejaVu", 11, fallback="Helvetica")
        p.drawString(20 * mm, H - 44 * mm, _safe(company_name))
        p.drawString(110 * mm, H - 44 * mm, _safe(counterparty_name))
        _set_font(p, "DejaVu", 9, fallback="Helvetica")
        p.drawString(
            20 * mm,
            H - 50 * mm,
            f"ИНН: {_safe(getattr(company,'inn',None))}    ОКПО: {_safe(getattr(company,'okpo',None))}",
        )
        p.drawString(
            110 * mm,
            H - 50 * mm,
            f"ИНН: {_safe(getattr(counterparty,'inn',None))}    ОКПО: {_safe(getattr(counterparty,'okpo',None))}",
        )
        p.drawString(
            20 * mm,
            H - 56 * mm,
            f"Р/с: {_safe(getattr(company,'score',None))}    БИК: {_safe(getattr(company,'bik',None))}",
        )
        p.drawString(
            110 * mm,
            H - 56 * mm,
            f"Р/с: {_safe(getattr(counterparty,'score',None))}    БИК: {_safe(getattr(counterparty,'bik',None))}",
        )
        p.drawString(20 * mm, H - 62 * mm, f"Адрес: {_safe(getattr(company,'address',None))}")
        p.drawString(110 * mm, H - 62 * mm, f"Адрес: {_safe(getattr(counterparty,'address',None))}")
        p.drawString(
            20 * mm,
            H - 68 * mm,
            f"Тел.: {_safe(getattr(company,'phone',None))}    E-mail: {_safe(getattr(company,'email',None))}",
        )
        p.drawString(
            110 * mm,
            H - 68 * mm,
            f"Тел.: {_safe(getattr(counterparty,'phone',None))}    E-mail: {_safe(getattr(counterparty,'email',None))}",
        )

        y = H - 78 * mm
        _set_font(p, "DejaVu-Bold", 9, fallback="Helvetica-Bold")
        p.drawString(20 * mm, y, "№")
        p.drawString(28 * mm, y, "Содержание записи")
        p.drawString(100 * mm, y, _safe(company_name))
        p.drawString(148 * mm, y, _safe(counterparty_name))

        y -= 5 * mm
        _set_font(p, "DejaVu-Bold", 9, fallback="Helvetica-Bold")
        p.drawString(100 * mm, y, "Дт")
        p.drawString(118 * mm, y, "Кт")
        p.drawString(148 * mm, y, "Дт")
        p.drawString(166 * mm, y, "Кт")
        p.line(20 * mm, y - 1 * mm, 190 * mm, y - 1 * mm)
        y -= 6 * mm

        _set_font(p, "DejaVu", 9, fallback="Helvetica")
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

        def ensure_page_space(current_y: float) -> float:
            if current_y < 40 * mm:
                p.showPage()
                _set_font(p, "DejaVu-Bold", 10, fallback="Helvetica-Bold")
                p.drawString(20 * mm, H - 20 * mm, "Продолжение акта сверки")
                yy = H - 30 * mm
                _set_font(p, "DejaVu-Bold", 9, fallback="Helvetica-Bold")
                p.drawString(20 * mm, yy, "№")
                p.drawString(28 * mm, yy, "Содержание записи")
                p.drawString(100 * mm, yy, _safe(company_name))
                p.drawString(148 * mm, yy, _safe(counterparty_name))
                yy -= 5 * mm
                p.drawString(100 * mm, yy, "Дт")
                p.drawString(118 * mm, yy, "Кт")
                p.drawString(148 * mm, yy, "Дт")
                p.drawString(166 * mm, yy, "Кт")
                p.line(20 * mm, yy - 1 * mm, 190 * mm, yy - 1 * mm)
                return yy - 6 * mm
            return current_y

        num = 0
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
        _set_font(p, "DejaVu-Bold", 10, fallback="Helvetica-Bold")
        p.drawString(28 * mm, y, "Итого обороты:")
        p.drawRightString(115 * mm, y, fmt(totals["a_debit"]))
        p.drawRightString(133 * mm, y, fmt(totals["a_credit"]))
        p.drawRightString(163 * mm, y, fmt(totals["b_debit"]))
        p.drawRightString(181 * mm, y, fmt(totals["b_credit"]))
        y -= 10 * mm

        debtor, creditor, amount = None, None, abs(closing)
        if closing > 0:
            debtor = counterparty_name
            creditor = company_name
        elif closing < 0:
            debtor = company_name
            creditor = counterparty_name

        _set_font(p, "DejaVu", 10, fallback="Helvetica")
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

        _set_font(p, "DejaVu-Bold", 10, fallback="Helvetica-Bold")
        p.drawString(20 * mm, y, _safe(company_name))
        p.drawString(110 * mm, y, _safe(counterparty_name))
        y -= 8 * mm
        _set_font(p, "DejaVu", 10, fallback="Helvetica")
        p.drawString(20 * mm, y, "Главный бухгалтер: __________________")
        p.drawString(110 * mm, y, "Главный бухгалтер: __________________")

        p.showPage()
        p.save()
        buf.seek(0)
        filename = f"counterparty_reconciliation_{counterparty.id}_{start_dt.date()}_{end_dt.date()}.pdf"
        return FileResponse(buf, as_attachment=True, filename=filename)

    def _error_pdf(self, message: str):
        buf = io.BytesIO()
        p = canvas.Canvas(buf, pagesize=A4)
        _set_font(p, "DejaVu-Bold", 14, fallback="Helvetica-Bold")
        p.drawString(30 * mm, 260 * mm, "Невозможно сформировать акт сверки")
        _set_font(p, "DejaVu", 11, fallback="Helvetica")
        p.drawString(30 * mm, 248 * mm, message)
        p.showPage()
        p.save()
        buf.seek(0)
        return FileResponse(buf, as_attachment=False, filename="reconciliation_error.pdf", status=400)
