"""
Партнёрство компаний (склад + касса). Все операции — только владелец/админ, агентам никогда (stock-partnership.md §7).
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from apps.users.models import Branch, Company
from apps.utils import _is_owner_like
from apps.warehouse import models, serializers_documents, serializers_money, services_money
from apps.warehouse import services_partnership as sp
from apps.warehouse.views import CompanyBranchRestrictedMixin

FORBIDDEN = "Партнёрство доступно только владельцу и администратору."
OP = models.PartnerOperationRequest


def _cash_registers_payload(partner_company, *, show_balance: bool):
    rows = []
    for cr in models.CashRegister.objects.filter(company=partner_company).select_related("branch").order_by("name"):
        rows.append(
            {
                "id": str(cr.id),
                "name": cr.name,
                "location": cr.location or "",
                "branch_id": str(cr.branch_id) if cr.branch_id else None,
                "branch_name": cr.branch.name if cr.branch_id else None,
                # D8: сальдо — только если партнёр разрешил забирать у него без подтверждения
                "balance": str(services_money.cash_register_balance(cr)) if show_balance else None,
            }
        )
    return rows


def _acting_company_for_user(user):
    return getattr(user, "owned_company", None) or getattr(user, "company", None)


def user_can_represent_company(user, company) -> bool:
    if not user or not company:
        return False
    oc = getattr(user, "owned_company", None)
    if oc and oc.id == company.id:
        return True
    if getattr(user, "company_id", None) == company.id:
        return True
    return False


def user_can_manage_partnership(user, company) -> bool:
    return _is_owner_like(user) and user_can_represent_company(user, company)


def user_can_decide_incoming_request(user, request_obj) -> bool:
    return user_can_manage_partnership(user, request_obj.to_company)


class _PartnershipBase(CompanyBranchRestrictedMixin, APIView):
    def manager_company(self, request):
        if self._agent_membership() is not None:
            raise PermissionDenied(FORBIDDEN)
        company = self._company()
        if not company:
            raise DRFValidationError({"company": "Компания не найдена."})
        if not user_can_manage_partnership(request.user, company):
            raise PermissionDenied(FORBIDDEN)
        return company

    def partner_and_partnership(self, my, partner_company_id):
        partner = Company.objects.filter(pk=partner_company_id).first()
        if partner is None or partner.id == my.id:
            raise NotFound("Компания-партнёр не найдена.")
        p = models.get_stock_partnership(my.id, partner.id)
        return partner, p

    def active_partnership(self, my, partner_company_id):
        partner, p = self.partner_and_partnership(my, partner_company_id)
        if p is None:
            raise PermissionDenied("Нет активного партнёрства с этой компанией.")
        return partner, p


# ---------------------------------------------------------------------------
# Заявки
# ---------------------------------------------------------------------------


class CompanyStockPartnershipRequestListCreateAPIView(_PartnershipBase):
    """GET входящие (ожидающие) и исходящие заявки; POST заявка {to_company, note}."""

    def get(self, request, *args, **kwargs):
        company = self.manager_company(request)
        incoming = (
            models.CompanyStockPartnershipRequest.objects.filter(
                to_company=company, status=models.CompanyStockPartnershipRequest.Status.PENDING
            )
            .select_related("from_company", "to_company", "created_by", "decided_by")
            .order_by("-created_at")
        )
        outgoing = (
            models.CompanyStockPartnershipRequest.objects.filter(from_company=company)
            .select_related("from_company", "to_company", "created_by", "decided_by")
            .order_by("-created_at")[:200]
        )
        return Response(
            {
                "incoming": serializers_documents.CompanyStockPartnershipRequestSerializer(incoming, many=True).data,
                "outgoing": serializers_documents.CompanyStockPartnershipRequestSerializer(outgoing, many=True).data,
            }
        )

    def post(self, request, *args, **kwargs):
        company = self.manager_company(request)
        ser = serializers_documents.CompanyStockPartnershipRequestCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        to_company = ser.validated_data["to_company"]
        if to_company.id == company.id:
            raise DRFValidationError({"to_company": ["Нельзя отправить запрос самой себе."]})
        if models.has_active_stock_partnership_between_ids(company.id, to_company.id):
            raise DRFValidationError({"to_company": ["Партнёрство с этой компанией уже активно."]})
        incoming = models.CompanyStockPartnershipRequest.objects.filter(
            from_company=to_company, to_company=company, status=models.CompanyStockPartnershipRequest.Status.PENDING
        ).first()
        if incoming is not None:  # П10: встречная заявка
            raise DRFValidationError({
                "to_company": ["У вас есть входящая заявка от этой компании — примите её."],
                "code": "incoming_request_exists",
                "request_id": str(incoming.id),
            })

        note = (ser.validated_data.get("note") or "").strip()[:512]
        try:
            obj = models.CompanyStockPartnershipRequest.objects.create(
                from_company=company,
                to_company=to_company,
                note=note,
                created_by=request.user,
                status=models.CompanyStockPartnershipRequest.Status.PENDING,
            )
        except IntegrityError:
            raise DRFValidationError({"to_company": ["Уже есть ожидающий запрос к этой компании."]})
        data = serializers_documents.CompanyStockPartnershipRequestSerializer(obj).data
        return Response(data, status=status.HTTP_201_CREATED)


class CompanyStockPartnershipRequestAcceptAPIView(_PartnershipBase):
    def post(self, request, pk=None, *args, **kwargs):
        self.manager_company(request)
        obj = get_object_or_404(
            models.CompanyStockPartnershipRequest.objects.select_related("from_company", "to_company"),
            pk=pk,
            status=models.CompanyStockPartnershipRequest.Status.PENDING,
        )
        if not user_can_decide_incoming_request(request.user, obj):
            raise PermissionDenied(FORBIDDEN)
        from django.utils import timezone

        with transaction.atomic():
            obj.status = models.CompanyStockPartnershipRequest.Status.ACCEPTED
            obj.decided_by = request.user
            obj.decided_at = timezone.now()
            obj.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
            sp.activate_from_request(obj, user=request.user)
        return Response(serializers_documents.CompanyStockPartnershipRequestSerializer(obj).data)


class CompanyStockPartnershipRequestRejectAPIView(_PartnershipBase):
    def post(self, request, pk=None, *args, **kwargs):
        self.manager_company(request)
        obj = get_object_or_404(
            models.CompanyStockPartnershipRequest.objects.select_related("from_company", "to_company"),
            pk=pk,
            status=models.CompanyStockPartnershipRequest.Status.PENDING,
        )
        if not user_can_decide_incoming_request(request.user, obj):
            raise PermissionDenied(FORBIDDEN)
        from django.utils import timezone

        obj.status = models.CompanyStockPartnershipRequest.Status.REJECTED
        obj.decided_by = request.user
        obj.decided_at = timezone.now()
        obj.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        return Response(serializers_documents.CompanyStockPartnershipRequestSerializer(obj).data)


class CompanyStockPartnershipRequestCancelAPIView(_PartnershipBase):
    def post(self, request, pk=None, *args, **kwargs):
        company = self.manager_company(request)
        obj = get_object_or_404(
            models.CompanyStockPartnershipRequest,
            pk=pk,
            from_company=company,
            status=models.CompanyStockPartnershipRequest.Status.PENDING,
        )
        from django.utils import timezone

        obj.status = models.CompanyStockPartnershipRequest.Status.CANCELLED
        obj.decided_by = request.user
        obj.decided_at = timezone.now()
        obj.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        return Response(serializers_documents.CompanyStockPartnershipRequestSerializer(obj).data)


# ---------------------------------------------------------------------------
# Партнёры, разрыв, настройки
# ---------------------------------------------------------------------------


class PartnerCompaniesListAPIView(_PartnershipBase):
    """GET stock-partnerships/active/ — партнёры с флагами (§7.2)."""

    def get(self, request, *args, **kwargs):
        company = self.manager_company(request)
        rows = (
            models.CompanyStockPartnership.objects.filter(
                Q(company_a=company) | Q(company_b=company), status=models.CompanyStockPartnership.Status.ACTIVE
            )
            .select_related("company_a", "company_b")
        )
        partners = sorted((sp.partner_row(p, company) for p in rows), key=lambda r: (r["name"] or "").lower())
        return Response({"partners": partners})


class PartnershipTerminateAPIView(_PartnershipBase):
    """POST stock-partnerships/companies/{partner_company_id}/terminate/ (§7.3)."""

    def post(self, request, partner_company_id=None, *args, **kwargs):
        company = self.manager_company(request)
        partner, p = self.partner_and_partnership(company, partner_company_id)
        if p is None:
            raise NotFound("Нет активного партнёрства с этой компанией.")
        p, cancelled = sp.terminate(p, company=company, user=request.user)
        return Response({
            "partner_company_id": str(partner.id),
            "partnership_id": str(p.id),
            "status": p.status,
            "terminated_at": p.terminated_at.isoformat(),
            "cancelled_operations": cancelled,
        })


class PartnershipSettingsAPIView(_PartnershipBase):
    """PATCH stock-partnerships/companies/{partner_company_id}/settings/ (§7.4)."""

    def patch(self, request, partner_company_id=None, *args, **kwargs):
        company = self.manager_company(request)
        partner, p = self.partner_and_partnership(company, partner_company_id)
        if p is None:
            raise NotFound("Нет активного партнёрства с этой компанией.")
        data = request.data if isinstance(request.data, dict) else {}
        unknown = [k for k in data if k not in sp.SETTINGS_FIELDS]
        if unknown or not data:
            raise DRFValidationError({
                k: ["Неизвестное поле."] for k in unknown
            } or {"detail": f"Передайте одно из полей: {', '.join(sp.SETTINGS_FIELDS)}."})
        bad = {k: ["Ожидается true или false."] for k, v in data.items() if not isinstance(v, bool)}
        if bad:
            raise DRFValidationError(bad)
        p = sp.change_settings(p, company=company, user=request.user, changes=data)
        return Response(sp.partner_row(p, company))


class PartnershipThrottle(UserRateThrottle):
    scope = "partnership_search"
    rate = "30/min"


class PartnershipCompanySearchAPIView(_PartnershipBase):
    """GET stock-partnerships/companies/search/?search= (§7.5): от 3 символов, ≤20, без своей компании."""

    throttle_classes = [PartnershipThrottle]

    def get(self, request, *args, **kwargs):
        company = self.manager_company(request)
        search = (request.query_params.get("search") or "").strip()[:128]
        if len(search) < 3:
            raise DRFValidationError({"search": ["Минимум 3 символа."]})
        found = list(Company.objects.filter(name__icontains=search).exclude(id=company.id).order_by("name")[:20])
        ids = [c.id for c in found]
        active = set()
        for p in models.CompanyStockPartnership.objects.filter(
            Q(company_a=company, company_b_id__in=ids) | Q(company_b=company, company_a_id__in=ids),
            status=models.CompanyStockPartnership.Status.ACTIVE,
        ):
            active.add(p.partner_id_of(company.id))
        pend = models.CompanyStockPartnershipRequest.Status.PENDING
        out_ids = set(models.CompanyStockPartnershipRequest.objects.filter(
            from_company=company, to_company_id__in=ids, status=pend).values_list("to_company_id", flat=True))
        in_ids = set(models.CompanyStockPartnershipRequest.objects.filter(
            to_company=company, from_company_id__in=ids, status=pend).values_list("from_company_id", flat=True))

        def _status(cid):
            if cid in active:
                return "ACTIVE"
            if cid in out_ids:
                return "PENDING_OUT"
            if cid in in_ids:
                return "PENDING_IN"
            return None

        return Response([{"id": str(c.id), "name": c.name, "partnership_status": _status(c.id)} for c in found])


# ---------------------------------------------------------------------------
# Склады, товары, кассы партнёра
# ---------------------------------------------------------------------------


class PartnerCompanyCatalogAPIView(_PartnershipBase):
    """
    Устаревший каталог (оставлен для старых сборок фронта; удалить через 2 релиза).
    GET .../companies/<company_id>/catalog/
    """

    def get(self, request, company_id=None, *args, **kwargs):
        my = self.manager_company(request)
        partner, p = self.active_partnership(my, company_id)

        bal_rows = models.StockBalance.objects.filter(warehouse__company=partner).values_list(
            "warehouse_id", "product_id", "qty"
        )
        balances = {(str(w), str(pr)): q for w, pr, q in bal_rows}
        warehouses_data = []
        for wh in models.Warehouse.objects.filter(company=partner).select_related("branch").order_by("name"):
            products_out = []
            for prod in models.WarehouseProduct.objects.filter(company=partner, warehouse=wh).order_by("name"):
                raw = balances.get((str(wh.id), str(prod.id)))
                qty = Decimal(raw or 0) if raw is not None else Decimal(prod.quantity or 0)
                products_out.append({
                    "id": str(prod.id), "name": prod.name, "article": prod.article or "",
                    "barcode": prod.barcode or "", "unit": prod.unit or "",
                    "qty": str(qty.quantize(Decimal("0.001"))),
                })
            warehouses_data.append({
                "id": str(wh.id), "name": wh.name,
                "branch_id": str(wh.branch_id) if wh.branch_id else None,
                "branch_name": wh.branch.name if wh.branch_id else None,
                "products": products_out,
            })
        return Response({
            "partner_company": {"id": str(partner.id), "name": partner.name},
            "warehouses": warehouses_data,
            "cash_registers": _cash_registers_payload(partner, show_balance=p.allows_direct_pull_from(partner.id)),
        })


class PartnerWarehousesAPIView(_PartnershipBase):
    """GET stock-partnerships/companies/{id}/warehouses/ — склады и кассы без товаров (§7.6)."""

    def get(self, request, partner_company_id=None, *args, **kwargs):
        my = self.manager_company(request)
        partner, p = self.active_partnership(my, partner_company_id)
        counts = dict(
            models.StockBalance.objects.filter(warehouse__company=partner, qty__gt=0)
            .values("warehouse_id").annotate(n=Count("product_id", distinct=True)).values_list("warehouse_id", "n")
        )
        whs = [
            {
                "id": str(wh.id), "name": wh.name,
                "branch_id": str(wh.branch_id) if wh.branch_id else None,
                "branch_name": wh.branch.name if wh.branch_id else None,
                "products_count": counts.get(wh.id, 0),
            }
            for wh in models.Warehouse.objects.filter(company=partner).select_related("branch").order_by("name")
        ]
        partner_allows = p.allows_direct_pull_from(partner.id)
        return Response({
            "partner_company": {"id": str(partner.id), "name": partner.name},
            "partnership": {
                "id": str(p.id),
                "since": (p.activated_at or p.created_at).isoformat(),
                "allow_direct_pull": p.allows_direct_pull_from(my.id),
                "partner_allows_direct_pull": partner_allows,
            },
            "warehouses": whs,
            "cash_registers": _cash_registers_payload(partner, show_balance=partner_allows),
        })


class _Pages(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


class PartnerWarehouseProductsAPIView(_PartnershipBase):
    """GET .../companies/{id}/warehouses/{warehouse_id}/products/?search=&page=&page_size= (§7.7)."""

    def get(self, request, partner_company_id=None, warehouse_id=None, *args, **kwargs):
        my = self.manager_company(request)
        partner, _p = self.active_partnership(my, partner_company_id)
        wh = models.Warehouse.objects.filter(pk=warehouse_id, company=partner).first()
        if wh is None:
            raise NotFound("Склад не найден.")
        qs = models.WarehouseProduct.objects.filter(company=partner, warehouse=wh).order_by("name", "id")
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(article__icontains=search) | Q(barcode__icontains=search))
        pager = _Pages()
        page = pager.paginate_queryset(qs.only("id", "name", "article", "barcode", "unit", "quantity", "warehouse_id"),
                                       request, view=self)
        ids = [prod.id for prod in page]
        bal = dict(models.StockBalance.objects.filter(warehouse=wh, product_id__in=ids).values_list("product_id", "qty"))
        results = []
        for prod in page:
            raw = bal.get(prod.id)
            qty = Decimal(raw or 0) if raw is not None else Decimal(prod.quantity or 0)
            results.append({
                "id": str(prod.id), "name": prod.name, "article": prod.article or "",
                "barcode": prod.barcode or "", "unit": prod.unit or "",
                "qty": str(qty.quantize(Decimal("0.001"))),
            })
        return pager.get_paginated_response(results)


# ---------------------------------------------------------------------------
# Перемещение и инкассация
# ---------------------------------------------------------------------------


class DocumentPartnerTransferCreateAPIView(_PartnershipBase):
    """
    POST stock-partnerships/transfer/ (§7.8).
    «Отдать» (склад-источник наш) или «забрать», если партнёр разрешил, — проводится сразу (201, result=posted);
    иначе — запрос на подтверждение партнёру (202, result=pending).
    """

    def post(self, request, *args, **kwargs):
        my_company = self.manager_company(request)
        ser = serializers_documents.TransferCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        wh_from = ser.validated_data["warehouse_from"]
        wh_to = ser.validated_data["warehouse_to"]

        if wh_from.company_id == wh_to.company_id:
            raise DRFValidationError(
                {"warehouse": ["Для перемещения внутри компании используйте POST /api/warehouse/transfer/."]}
            )
        if my_company.id not in (wh_from.company_id, wh_to.company_id):
            raise DRFValidationError({"warehouse": ["Один из складов должен принадлежать вашей компании."]})
        partnership = models.get_stock_partnership(wh_from.company_id, wh_to.company_id)
        if partnership is None:
            raise DRFValidationError({"warehouse": ["Между компаниями этих складов нет принятого партнёрства."]})

        items = [{"product": it["product"], "qty": it["qty"]} for it in ser.validated_data["items"]]
        for it in items:
            if it["qty"] <= 0:
                raise DRFValidationError({"items": ["Количество должно быть больше 0."]})
            if it["product"].warehouse_id != wh_from.id:
                raise DRFValidationError({"items": [f"Товар «{it['product'].name}» не со склада-источника."]})
        comment = ser.validated_data.get("comment") or ""

        direct = wh_from.company_id == my_company.id or partnership.allows_direct_pull_from(wh_from.company_id)
        try:
            if direct:
                doc = sp.execute_transfer(
                    warehouse_from=wh_from, warehouse_to=wh_to, items=items, comment=comment,
                    created_by=request.user, initiator_company=my_company,
                )
            else:
                op = sp.create_transfer_request(
                    partnership=partnership, initiator_company=my_company, warehouse_from=wh_from,
                    warehouse_to=wh_to, items=items, comment=comment, user=request.user,
                )
        except sp.PartnershipError as e:
            raise DRFValidationError({"detail": str(e)})

        if not direct:
            data = serializers_documents.PartnerOperationSerializer(op).data
            return Response({"result": "pending", "operation": data}, status=status.HTTP_202_ACCEPTED)
        out = serializers_documents.DocumentSerializer(doc, context={"request": request}).data
        out["result"] = "posted"
        return Response(out, status=status.HTTP_201_CREATED)


class PartnerCashIncassationListCreateAPIView(_PartnershipBase):
    """GET история инкассаций; POST инкассация (§7.9): из своей кассы — сразу, из кассы партнёра — с подтверждением."""

    def get(self, request, *args, **kwargs):
        company = self.manager_company(request)
        qs = (
            models.CompanyCashIncassation.objects.filter(Q(from_company=company) | Q(to_company=company))
            .select_related("from_company", "to_company", "cash_register_from", "cash_register_to",
                            "expense_document", "receipt_document", "created_by")
            .order_by("-created_at")[:200]
        )
        return Response({"results": serializers_money.CompanyCashIncassationSerializer(qs, many=True).data})

    def post(self, request, *args, **kwargs):
        my_company = self.manager_company(request)
        ser = serializers_money.PartnerCashIncassationCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cr_from = ser.validated_data["cash_register_from"]
        cr_to = ser.validated_data["cash_register_to"]
        if my_company.id not in (cr_from.company_id, cr_to.company_id):
            raise DRFValidationError({"cash_register": ["Одна из касс должна принадлежать вашей компании."]})
        partnership = models.get_stock_partnership(cr_from.company_id, cr_to.company_id)
        if partnership is None:
            raise DRFValidationError({"detail": "Между компаниями этих касс нет принятого партнёрства."})
        amount = ser.validated_data["amount"]
        comment = ser.validated_data.get("comment") or ""

        if cr_from.id == cr_to.id or cr_from.company_id == cr_to.company_id:
            raise DRFValidationError({"cash_register": ["Кассы должны принадлежать разным компаниям."]})
        direct = cr_from.company_id == my_company.id or partnership.allows_direct_pull_from(cr_from.company_id)
        try:
            if direct:
                inc = sp.execute_incassation(cash_register_from=cr_from, cash_register_to=cr_to, amount=amount,
                                             comment=comment, created_by=request.user)
            else:
                if amount <= 0:
                    raise sp.PartnershipError("Сумма должна быть больше 0.")
                op = sp.create_incassation_request(
                    partnership=partnership, initiator_company=my_company, cash_register_from=cr_from,
                    cash_register_to=cr_to, amount=amount, comment=comment, user=request.user,
                )
        except sp.PartnershipError as e:
            raise DRFValidationError({"detail": str(e)})
        if not direct:
            data = serializers_documents.PartnerOperationSerializer(op).data
            return Response({"result": "pending", "operation": data}, status=status.HTTP_202_ACCEPTED)
        out = serializers_money.CompanyCashIncassationSerializer(inc).data
        out["result"] = "posted"
        return Response(out, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Операции с подтверждением (§7.10)
# ---------------------------------------------------------------------------


def _ops_qs():
    return OP.objects.select_related(
        "initiator_company", "source_company", "warehouse_from", "warehouse_to",
        "cash_register_from", "cash_register_to", "created_by", "decided_by", "document",
    )


class PartnerOperationListAPIView(_PartnershipBase):
    def get(self, request, *args, **kwargs):
        company = self.manager_company(request)
        st = (request.query_params.get("status") or "").strip().upper()
        kind = (request.query_params.get("kind") or "").strip().upper()
        inc = _ops_qs().filter(source_company=company)
        out = _ops_qs().filter(initiator_company=company)
        if kind:
            inc, out = inc.filter(kind=kind), out.filter(kind=kind)
        if st:
            incoming = list(inc.filter(status=st).order_by("-created_at")[:200])
            out = out.filter(status=st)
        else:
            incoming = list(inc.filter(status=OP.Status.PENDING).order_by("-created_at")) + list(
                inc.exclude(status=OP.Status.PENDING).order_by("-updated_at")[:50]
            )
        outgoing = list(out.order_by("-created_at")[:200])
        ser = serializers_documents.PartnerOperationSerializer
        return Response({"incoming": ser(incoming, many=True).data, "outgoing": ser(outgoing, many=True).data})


class _PartnerOperationActionBase(_PartnershipBase):
    side = "source"  # чья сторона решает

    def get_op(self, request, pk):
        company = self.manager_company(request)
        op = get_object_or_404(OP, pk=pk)
        owner_id = op.source_company_id if self.side == "source" else op.initiator_company_id
        if owner_id != company.id:
            if company.id in (op.source_company_id, op.initiator_company_id):
                raise PermissionDenied("Это действие доступно другой стороне операции.")
            raise NotFound("Операция не найдена.")
        return op

    def respond(self, op):
        fresh = _ops_qs().get(pk=op.pk)
        return Response(serializers_documents.PartnerOperationSerializer(fresh).data)


class PartnerOperationApproveAPIView(_PartnerOperationActionBase):
    def post(self, request, pk=None, *args, **kwargs):
        op = self.get_op(request, pk)
        try:
            op = sp.approve_operation(op.pk, user=request.user)
        except sp.PartnershipError as e:
            raise DRFValidationError({"detail": str(e)})
        return self.respond(op)


class PartnerOperationRejectAPIView(_PartnerOperationActionBase):
    def post(self, request, pk=None, *args, **kwargs):
        op = self.get_op(request, pk)
        reason = str((request.data or {}).get("reason") or "") if isinstance(request.data, dict) else ""
        try:
            op = sp.decide_operation(op.pk, user=request.user, status=OP.Status.REJECTED, reason=reason)
        except sp.PartnershipError as e:
            raise DRFValidationError({"detail": str(e)})
        return self.respond(op)


class PartnerOperationCancelAPIView(_PartnerOperationActionBase):
    side = "initiator"

    def post(self, request, pk=None, *args, **kwargs):
        op = self.get_op(request, pk)
        try:
            op = sp.decide_operation(op.pk, user=request.user, status=OP.Status.CANCELLED)
        except sp.PartnershipError as e:
            raise DRFValidationError({"detail": str(e)})
        return self.respond(op)


# ---------------------------------------------------------------------------
# История продаж партнёра (§7.15–7.16)
# ---------------------------------------------------------------------------

SALE_DOC_TYPES = (models.Document.DocType.SALE, models.Document.DocType.SALE_RETURN)
SALE_STATUSES = (models.Document.Status.POSTED, models.Document.Status.CASH_PENDING)


class _PartnerSalesBase(_PartnershipBase):
    def scope(self, request, partner_company_id):
        my = self.manager_company(request)
        partner, p = self.active_partnership(my, partner_company_id)
        if not p.shares_sales_history_of(partner.id):
            raise PermissionDenied({"detail": "Партнёр скрыл историю продаж.", "code": "sales_history_hidden"})
        from apps.warehouse.views_analytics import _resolve_partner_branch_scope

        branch, all_branches = _resolve_partner_branch_scope(request, partner)
        return partner, branch, all_branches

    @staticmethod
    def partner_docs(partner, branch, all_branches, doc_types, statuses, dt_from=None, dt_to_excl=None):
        """Документы партнёра по правилу A13 (как в аналитике партнёра) — без периода, если он не задан."""
        from apps.warehouse.analytics import _apply_branch_scope

        item_qs = _apply_branch_scope(
            models.DocumentItem.objects.filter(product__company=partner), branch,
            path="product__branch", all_branches=all_branches,
        )
        wh_q = Q(warehouse_from__company=partner)
        if not all_branches:
            wh_q &= Q(warehouse_from__branch=branch) if branch is not None else Q(warehouse_from__branch__isnull=True)
        qs = models.Document.objects.filter(
            wh_q | Q(warehouse_from__isnull=True, id__in=item_qs.values("document_id")),
            doc_type__in=tuple(doc_types), status__in=tuple(statuses),
        )
        if dt_from is not None:
            qs = qs.filter(date__gte=dt_from, date__lt=dt_to_excl)
        return qs

    @staticmethod
    def row(doc):
        agent = doc.agent
        agent_name = None
        if agent is not None:
            agent_name = (f"{agent.first_name or ''} {agent.last_name or ''}".strip()
                          or getattr(agent, "email", None))
        wf = doc.warehouse_from
        return {
            "id": str(doc.id),
            "doc_type": doc.doc_type,
            "number": doc.number,
            "date": doc.date.isoformat() if doc.date else None,
            "status": doc.status,
            "payment_kind": doc.payment_kind,
            "warehouse_from": str(wf.id) if wf else None,
            "warehouse_from_name": wf.name if wf else None,
            "branch_name": wf.branch.name if wf and wf.branch_id else None,
            "counterparty_display_name": doc.counterparty.name if doc.counterparty_id else None,
            "agent_display": agent_name,
        }


class PartnerSalesListAPIView(_PartnerSalesBase):
    """GET stock-partnerships/companies/{id}/sales/ (§7.15)."""

    def get(self, request, partner_company_id=None, *args, **kwargs):
        from apps.warehouse.analytics import _dt_range, _parse_period

        partner, branch, all_branches = self.scope(request, partner_company_id)
        q = request.query_params
        period = _parse_period(request)
        if (period["date_to"] - period["date_from"]).days > 366:
            raise DRFValidationError({"date_from": ["Период не больше 366 дней."]})
        dt_from, dt_to_excl = _dt_range(period["date_from"], period["date_to"])

        doc_type = (q.get("doc_type") or "SALE").strip().upper()
        if doc_type not in SALE_DOC_TYPES:
            raise DRFValidationError({"doc_type": ["SALE или SALE_RETURN."]})
        st = (q.get("status") or "").strip().upper()
        if st and st not in SALE_STATUSES:
            raise DRFValidationError({"status": ["POSTED или CASH_PENDING."]})
        statuses = (st,) if st else SALE_STATUSES

        qs = self.partner_docs(partner, branch, all_branches, (doc_type,), statuses, dt_from, dt_to_excl)
        search = (q.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(number__icontains=search) | Q(counterparty__name__icontains=search))

        ids = qs.values("id")
        base = models.Document.objects.filter(id__in=ids)
        line_disc = models.DocumentItem.objects.filter(document_id__in=ids).aggregate(
            qty=Sum("qty"), disc=Sum("discount_amount"))
        doc_agg = base.aggregate(n=Count("id"), amount=Sum("total"), disc=Sum("discount_amount"))
        summary = {
            "count": doc_agg["n"] or 0,
            "amount": str(Decimal(doc_agg["amount"] or 0).quantize(Decimal("0.01"))),
            "discount_amount": str(
                (Decimal(doc_agg["disc"] or 0) + Decimal(line_disc["disc"] or 0)).quantize(Decimal("0.01"))),
            "items_qty": str(Decimal(line_disc["qty"] or 0).quantize(Decimal("0.001"))),
        }

        page_qs = (
            base.select_related("warehouse_from__branch", "counterparty", "agent")
            .annotate(items_count=Count("items"), items_qty=Sum("items__qty"), lines_disc=Sum("items__discount_amount"))
            .order_by("-date", "-number")
        )
        pager = _Pages()
        page = pager.paginate_queryset(page_qs, request, view=self)
        results = []
        for d in page:
            r = self.row(d)
            r["items_count"] = d.items_count or 0
            r["items_qty"] = str(Decimal(d.items_qty or 0).quantize(Decimal("0.001")))
            r["discount_amount"] = str(
                (Decimal(d.discount_amount or 0) + Decimal(d.lines_disc or 0)).quantize(Decimal("0.01")))
            r["total"] = str(Decimal(d.total or 0).quantize(Decimal("0.01")))
            results.append(r)
        resp = pager.get_paginated_response(results)
        resp.data = {
            "partner_company": {"id": str(partner.id), "name": partner.name},
            "date_from": period["date_from"].isoformat(),
            "date_to": period["date_to"].isoformat(),
            "summary": summary,
            **resp.data,
        }
        return resp


class PartnerSaleDetailAPIView(_PartnerSalesBase):
    """GET stock-partnerships/companies/{id}/sales/{document_id}/ (§7.16). Без комментариев, реквизитов, себестоимости."""

    def get(self, request, partner_company_id=None, document_id=None, *args, **kwargs):
        partner, _branch, _all = self.scope(request, partner_company_id)
        doc = (
            self.partner_docs(partner, None, True, SALE_DOC_TYPES, SALE_STATUSES)
            .filter(id=document_id)
            .select_related("warehouse_from__branch", "counterparty", "agent")
            .first()
        )
        if doc is None:
            raise NotFound("Документ не найден.")
        out = self.row(doc)
        out["discount_percent"] = str(Decimal(doc.discount_percent or 0).quantize(Decimal("0.01")))
        out["discount_amount"] = str(Decimal(doc.discount_amount or 0).quantize(Decimal("0.01")))
        out["total"] = str(Decimal(doc.total or 0).quantize(Decimal("0.01")))
        items = []
        for it in doc.items.select_related("product").order_by("id"):
            qty = Decimal(it.qty or 0)
            price = Decimal(it.price or 0)
            disc = Decimal(it.discount_amount or 0)
            net = getattr(it, "net_amount", None)
            net = Decimal(net) if net not in (None, "") and Decimal(net) != 0 else qty * price - disc
            items.append({
                "id": str(it.id),
                "product_name": it.product.name if it.product_id else None,
                "product_article": (it.product.article or "") if it.product_id else "",
                "unit": (it.product.unit or "") if it.product_id else "",
                "qty": str(qty.quantize(Decimal("0.001"))),
                "price": str(price.quantize(Decimal("0.01"))),
                "discount_percent": str(Decimal(it.discount_percent or 0).quantize(Decimal("0.01"))),
                "discount_amount": str(disc.quantize(Decimal("0.01"))),
                "net_amount": str(net.quantize(Decimal("0.01"))),
            })
        out["items"] = items
        return Response(out)
