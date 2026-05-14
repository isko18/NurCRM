from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import Company
from apps.utils import _is_owner_like
from apps.warehouse import models, serializers_documents, services
from apps.warehouse.views import CompanyBranchRestrictedMixin


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


def user_can_decide_incoming_request(user, request_obj) -> bool:
    if not _is_owner_like(user):
        return False
    to_c = request_obj.to_company
    oc = getattr(user, "owned_company", None)
    if oc and oc.id == to_c.id:
        return True
    if getattr(user, "company_id", None) == to_c.id:
        return True
    return False


class CompanyStockPartnershipRequestListCreateAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    GET: входящие (ожидающие) и исходящие заявки на партнёрство по складу.
    POST: отправить заявку в другую компанию { "to_company": "<uuid>", "note": "..." }.
    """

    def get(self, request, *args, **kwargs):
        company = self._company()
        if not company:
            raise DRFValidationError({"company": "Компания не найдена."})
        if not user_can_represent_company(request.user, company):
            raise PermissionDenied()

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
        company = self._company()
        if not company:
            raise DRFValidationError({"company": "Компания не найдена."})
        if not user_can_represent_company(request.user, company):
            raise PermissionDenied()

        ser = serializers_documents.CompanyStockPartnershipRequestCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        to_company = ser.validated_data["to_company"]
        if to_company.id == company.id:
            raise DRFValidationError({"to_company": "Нельзя отправить запрос самой себе."})

        if models.has_active_stock_partnership_between_ids(company.id, to_company.id):
            raise DRFValidationError({"to_company": "Партнёрство с этой компанией уже активно."})

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
            raise DRFValidationError({"to_company": "Уже есть ожидающий запрос к этой компании."})

        data = serializers_documents.CompanyStockPartnershipRequestSerializer(obj).data
        return Response(data, status=status.HTTP_201_CREATED)


class CompanyStockPartnershipRequestAcceptAPIView(CompanyBranchRestrictedMixin, APIView):
    def post(self, request, pk=None, *args, **kwargs):
        obj = get_object_or_404(
            models.CompanyStockPartnershipRequest.objects.select_related("from_company", "to_company"),
            pk=pk,
            status=models.CompanyStockPartnershipRequest.Status.PENDING,
        )
        if not user_can_decide_incoming_request(request.user, obj):
            raise PermissionDenied()

        with transaction.atomic():
            obj.status = models.CompanyStockPartnershipRequest.Status.ACCEPTED
            obj.decided_by = request.user
            obj.decided_at = timezone.now()
            obj.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])

            id_lo, id_hi = models.canonical_company_pair_ids(obj.from_company_id, obj.to_company_id)
            models.CompanyStockPartnership.objects.get_or_create(
                company_a_id=id_lo,
                company_b_id=id_hi,
            )

        return Response(serializers_documents.CompanyStockPartnershipRequestSerializer(obj).data)


class CompanyStockPartnershipRequestRejectAPIView(CompanyBranchRestrictedMixin, APIView):
    def post(self, request, pk=None, *args, **kwargs):
        obj = get_object_or_404(
            models.CompanyStockPartnershipRequest.objects.select_related("from_company", "to_company"),
            pk=pk,
            status=models.CompanyStockPartnershipRequest.Status.PENDING,
        )
        if not user_can_decide_incoming_request(request.user, obj):
            raise PermissionDenied()
        obj.status = models.CompanyStockPartnershipRequest.Status.REJECTED
        obj.decided_by = request.user
        obj.decided_at = timezone.now()
        obj.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        return Response(serializers_documents.CompanyStockPartnershipRequestSerializer(obj).data)


class CompanyStockPartnershipRequestCancelAPIView(CompanyBranchRestrictedMixin, APIView):
    def post(self, request, pk=None, *args, **kwargs):
        company = self._company()
        if not company:
            raise DRFValidationError({"company": "Компания не найдена."})
        if not user_can_represent_company(request.user, company):
            raise PermissionDenied()
        obj = get_object_or_404(
            models.CompanyStockPartnershipRequest,
            pk=pk,
            from_company=company,
            status=models.CompanyStockPartnershipRequest.Status.PENDING,
        )
        obj.status = models.CompanyStockPartnershipRequest.Status.CANCELLED
        obj.decided_by = request.user
        obj.decided_at = timezone.now()
        obj.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        return Response(serializers_documents.CompanyStockPartnershipRequestSerializer(obj).data)


class PartnerCompaniesListAPIView(CompanyBranchRestrictedMixin, APIView):
    """Активные компании-партнёры по складу для текущей компании пользователя."""

    def get(self, request, *args, **kwargs):
        company = self._company()
        if not company:
            raise DRFValidationError({"company": "Компания не найдена."})
        if not user_can_represent_company(request.user, company):
            raise PermissionDenied()

        qs = models.CompanyStockPartnership.objects.filter(
            Q(company_a=company) | Q(company_b=company)
        ).select_related("company_a", "company_b")

        partners = []
        seen = set()
        for row in qs:
            partner = row.company_b if row.company_a_id == company.id else row.company_a
            if partner.id in seen:
                continue
            seen.add(partner.id)
            partners.append({"id": str(partner.id), "name": partner.name})

        return Response({"partners": partners})


class PartnerCompanyCatalogAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    Склады партнёрской компании и товары с остатком (для выбора позиций перед перемещением).
    GET .../companies/<company_id>/catalog/
    """

    def get(self, request, company_id=None, *args, **kwargs):
        my = self._company()
        if not my:
            raise DRFValidationError({"company": "Компания не найдена."})
        if not user_can_represent_company(request.user, my):
            raise PermissionDenied()

        partner = get_object_or_404(Company.objects.all(), pk=company_id)
        if partner.id == my.id:
            raise DRFValidationError({"detail": "Укажите компанию-партнёра."})
        if not models.has_active_stock_partnership_between_ids(my.id, partner.id):
            return Response({"detail": "Нет активного партнёрства с этой компанией."}, status=status.HTTP_403_FORBIDDEN)

        warehouses_data = []
        wh_qs = models.Warehouse.objects.filter(company=partner).select_related("branch").order_by("name")

        for wh in wh_qs:
            balances = {
                str(b.product_id): b.qty
                for b in models.StockBalance.objects.filter(warehouse=wh).only("product_id", "qty")
            }
            products_out = []
            prod_qs = (
                models.WarehouseProduct.objects.filter(company=partner, warehouse=wh)
                .select_related("brand", "category")
                .order_by("name")
            )
            for p in prod_qs:
                raw = balances.get(str(p.id))
                if raw is None:
                    qty = Decimal(p.quantity or 0) if p.warehouse_id == wh.id else Decimal(0)
                else:
                    qty = Decimal(raw or 0)
                products_out.append(
                    {
                        "id": str(p.id),
                        "name": p.name,
                        "article": p.article or "",
                        "barcode": p.barcode or "",
                        "unit": p.unit or "",
                        "qty": str(qty.quantize(Decimal("0.001"))),
                    }
                )
            warehouses_data.append(
                {
                    "id": str(wh.id),
                    "name": wh.name,
                    "branch_id": str(wh.branch_id) if wh.branch_id else None,
                    "branch_name": wh.branch.name if wh.branch_id else None,
                    "products": products_out,
                }
            )

        return Response(
            {
                "partner_company": {"id": str(partner.id), "name": partner.name},
                "warehouses": warehouses_data,
            }
        )


class DocumentPartnerTransferCreateAPIView(CompanyBranchRestrictedMixin, APIView):
    """
    Межкомпанейское перемещение (TRANSFER): тело как у POST /api/warehouse/transfer/.
    Требуется активное партнёрство; один из складов должен быть вашей компании.
    """

    def post(self, request, *args, **kwargs):
        if self._agent_membership() is not None:
            raise PermissionDenied("Межкомпанейское перемещение недоступно для агентов.")

        ser = serializers_documents.TransferCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        my_company = self._company()
        if not my_company:
            raise DRFValidationError({"company": "Компания не найдена."})
        if not user_can_represent_company(request.user, my_company):
            raise PermissionDenied()

        wh_from = ser.validated_data["warehouse_from"]
        wh_to = ser.validated_data["warehouse_to"]

        if wh_from.company_id == wh_to.company_id:
            raise DRFValidationError(
                {"warehouse": "Для перемещения внутри компании используйте POST /api/warehouse/transfer/."}
            )

        if my_company.id not in (wh_from.company_id, wh_to.company_id):
            raise DRFValidationError({"warehouse": "Один из складов должен принадлежать вашей компании."})

        if not models.has_active_stock_partnership_between_ids(wh_from.company_id, wh_to.company_id):
            raise DRFValidationError({"warehouse": "Между компаниями этих складов нет принятого партнёрства."})

        self._ensure_agent_can_access_warehouse(wh_from, field_name="warehouse_from")
        self._ensure_agent_can_access_warehouse(wh_to, field_name="warehouse_to")

        doc = models.Document.objects.create(
            doc_type=models.Document.DocType.TRANSFER,
            warehouse_from=wh_from,
            warehouse_to=wh_to,
            comment=ser.validated_data.get("comment") or "",
        )

        for it in ser.validated_data["items"]:
            item = models.DocumentItem(document=doc, **it)
            try:
                item.clean()
            except Exception as e:
                raise DRFValidationError(getattr(e, "message_dict", {"detail": str(e)}))
            item.save()

        try:
            services.post_document(doc)
        except Exception as e:
            raise DRFValidationError({"detail": str(e)})

        out = serializers_documents.DocumentSerializer(doc, context={"request": request}).data
        return Response(out, status=status.HTTP_201_CREATED)
