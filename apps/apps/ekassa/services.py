from typing import Any, Dict, Optional

from apps.ekassa.client import EkassaHttpClient
from apps.ekassa.exceptions import EkassaConfigurationError
from apps.ekassa.models import EkassaIntegration
from apps.users.models import Company


def get_integration(company: Company):
    return EkassaIntegration.objects.filter(company=company).first()


def require_ready_integration(company: Company) -> EkassaIntegration:
    cfg = get_integration(company)
    if cfg is None or not cfg.is_ready():
        raise EkassaConfigurationError("Интеграция eKassa выключена или не настроена.")
    return cfg


def client_for(company: Company) -> EkassaHttpClient:
    return EkassaHttpClient(require_ready_integration(company))


def inject_fiscal_number(body: Optional[Dict[str, Any]], fiscal_number: str) -> Dict[str, Any]:
    out = dict(body or {})
    out.setdefault("fiscal_number", fiscal_number)
    return out
