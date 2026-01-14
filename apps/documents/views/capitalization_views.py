from apps.documents.mixins import CompanyBranchRestrictedMixin

from rest_framework import generics


class CapitalizationCreateUpdateDeleted(CompanyBranchRestrictedMixin):
    serializers_class = 

    def get_queryset():
        pass






