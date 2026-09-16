import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.construction.models import CashFlow, CashShift
from apps.construction.utils import is_owner_like

logger = logging.getLogger(__name__)


def send_cashflow_ws_notification(company_id, event, data):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        company_group = f"notif_company_{company_id}"
        async_to_sync(channel_layer.group_send)(
            company_group,
            {
                "type": "market.notification",
                "event": event,
                "data": data,
            },
        )
    except Exception as e:
        logger.warning("Failed to send cashflow ws notification: %s", e)


@transaction.atomic
def resolve_change_request(flow: CashFlow, new_status: str, user=None) -> CashFlow:
    """
    Применение или отклонение заявки на редактирование/отмену движения кассы (V2).
    """
    if flow.request_kind not in (CashFlow.RequestKind.EDIT, CashFlow.RequestKind.CANCEL):
        return flow

    if not is_owner_like(user):
        raise PermissionDenied("Только владелец или управляющий может одобрять или отклонять заявки.")

    locked_flow = CashFlow.objects.select_for_update().filter(id=flow.id).first()
    if not locked_flow:
        return flow
    flow = locked_flow

    if flow.status != CashFlow.Status.PENDING:
        if flow.status == new_status:
            return flow
        raise ValidationError({"detail": "Заявка уже была разрешена ранее."})

    target_flow = CashFlow.objects.select_for_update().filter(id=flow.target_flow_id).first()
    if not target_flow:
        raise ValidationError({"detail": "Целевое движение кассы не найдено."})

    if new_status == CashFlow.Status.APPROVED:
        if target_flow.status != CashFlow.Status.APPROVED:
            raise ValidationError({"detail": "Целевое движение уже не является одобренным."})

        # Проверка других открытых заявок
        other_pending = CashFlow.objects.filter(
            target_flow=target_flow,
            status=CashFlow.Status.PENDING,
        ).exclude(id=flow.id).exists()
        if other_pending:
            raise ValidationError({"detail": "По этому движению есть другая открытая заявка."})

        if flow.request_kind == CashFlow.RequestKind.EDIT:
            proposed = flow.proposed or {}
            new_name = proposed.get("name")
            new_amount = proposed.get("amount")
            new_type = proposed.get("type")

            if new_name is not None:
                target_flow.name = str(new_name).strip()
            if new_amount is not None:
                target_flow.amount = Decimal(str(new_amount))
            if new_type is not None:
                target_flow.type = str(new_type).strip().lower()

            target_flow.save()

            flow.status = CashFlow.Status.APPROVED
            flow.resolved_by = user
            flow.resolved_at = timezone.now()
            flow.save(update_fields=["status", "resolved_by", "resolved_at"])

            send_cashflow_ws_notification(
                flow.company_id,
                "market.cashflow.updated",
                {
                    "cashflow_id": str(target_flow.id),
                    "request_id": str(flow.id),
                    "type": target_flow.type,
                    "amount": str(target_flow.amount),
                    "cashbox_id": str(target_flow.cashbox_id) if target_flow.cashbox_id else None,
                    "status": target_flow.status,
                },
            )

        elif flow.request_kind == CashFlow.RequestKind.CANCEL:
            # Исключаем target_flow из агрегатов (статус rejected)
            # Внимание (R5): НЕ трогаем исходную бизнес-операцию (продажу/закупку/долг)
            target_flow.status = CashFlow.Status.REJECTED
            target_flow.save(update_fields=["status"])

            flow.status = CashFlow.Status.APPROVED
            flow.resolved_by = user
            flow.resolved_at = timezone.now()
            flow.save(update_fields=["status", "resolved_by", "resolved_at"])

            send_cashflow_ws_notification(
                flow.company_id,
                "market.cashflow.deleted",
                {
                    "cashflow_id": str(target_flow.id),
                    "request_id": str(flow.id),
                    "cashbox_id": str(target_flow.cashbox_id) if target_flow.cashbox_id else None,
                    "status": target_flow.status,
                },
            )

    elif new_status == CashFlow.Status.REJECTED:
        flow.status = CashFlow.Status.REJECTED
        flow.resolved_by = user
        flow.resolved_at = timezone.now()
        flow.save(update_fields=["status", "resolved_by", "resolved_at"])

    return flow
