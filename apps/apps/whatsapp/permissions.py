# apps/integrations/permissions.py
from rest_framework.permissions import BasePermission

class IsCompanyMember(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        company_id = str(view.kwargs.get("company_id"))
        return user.is_authenticated and str(user.company_id) == company_id and \
               getattr(user.company, "can_view_whatsapp", False)

class IsNodeWebhook(BasePermission):
    # Простой вариант по токену в заголовке
    def has_permission(self, request, view):
        from django.conf import settings
        token = request.headers.get("X-WA-TOKEN")
        return token and token == getattr(settings, "WHATSAPP_NODE_TOKEN", "")
