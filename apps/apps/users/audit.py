from .models import PlatformAdminAuditLog


SENSITIVE_PAYLOAD_TOKENS = ("password", "token", "refresh")


def _client_ip(request):
    if not request:
        return None
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR")


def sanitize_audit_payload(value):
    if not isinstance(value, dict):
        return {}

    clean = {}
    for key, item in value.items():
        key_text = str(key)
        lowered = key_text.lower()
        if any(token in lowered for token in SENSITIVE_PAYLOAD_TOKENS):
            clean[key_text] = "[redacted]"
        elif isinstance(item, dict):
            clean[key_text] = sanitize_audit_payload(item)
        elif isinstance(item, list):
            clean[key_text] = [
                sanitize_audit_payload(child) if isinstance(child, dict) else child
                for child in item
            ]
        else:
            clean[key_text] = item
    return clean


def create_platform_admin_audit_log(
    *,
    actor,
    action,
    object_type,
    object_id,
    company_id=None,
    payload=None,
    request=None,
):
    return PlatformAdminAuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        company_id=str(company_id) if company_id else None,
        payload=sanitize_audit_payload(payload or {}),
        ip=_client_ip(request),
    )
