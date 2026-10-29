# apps/integrations/auth.py
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CompanyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["company_id"] = str(user.company_id) if user.company_id else None
        token["email"] = user.email
        return token
