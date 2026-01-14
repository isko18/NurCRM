

from apps.documents.models import Document

from rest_framework import serializers

from apps.documents.serializers.mixins import CompanyBranchReadOnlyMixin



class DocumentSerializer(CompanyBranchReadOnlyMixin, serializers.ModelSerializer):

    class Meta:
        model = Document
        fields = ("id","document_type","carried_out","document_status",
                "created_at","updated_at"
        )
        read_only_field = ("updated_at","created_at")

