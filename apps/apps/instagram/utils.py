from django.shortcuts import get_object_or_404
from .models import CompanyIGAccount


def get_company_account_or_404(request, pk):
    return get_object_or_404(
        CompanyIGAccount.objects.filter(company_id=request.user.company_id, is_active=True),
        pk=pk,
    )