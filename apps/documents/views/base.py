
from rest_framework import generics

from .mixins import CompanyBranchRestrictedMixin

from apps.documents.serializers import DocumentSerializer


# Модуль для базового документа 
# ну тут в основном фильтрация

class DocumentListView(CompanyBranchRestrictedMixin,generics.ListAPIView):
    pass


