from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from instagrapi.exceptions import TwoFactorRequired, ChallengeRequired, PleaseWaitFewMinutes

from .models import CompanyIGAccount
from .serializers import AccountConnectSerializer, CompanyIGAccountOutSerializer
from .service import IGChatService
from .utils import get_company_account_or_404


class AccountsListMyCompany(APIView):
    """Список IG-аккаунтов только моей компании."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not getattr(request.user, "company_id", None):
            return Response({"detail": "user_has_no_company"}, status=400)
        qs = CompanyIGAccount.objects.filter(
            company_id=request.user.company_id, is_active=True
        ).order_by("username")
        return Response(CompanyIGAccountOutSerializer(qs, many=True).data)


class AccountConnectLoginView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        user = request.user
        if not getattr(user, "company_id", None):
            return Response({"detail":"user_has_no_company"}, status=400)

        ser = AccountConnectSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        username = ser.validated_data["username"].strip()
        password = ser.validated_data["password"]
        code = ser.validated_data.get("code") or None

        acc = CompanyIGAccount.objects.filter(
            company_id=user.company_id, username=username
        ).first()
        if not acc:
            acc = CompanyIGAccount.objects.create(
                company_id=user.company_id, username=username
            )

        svc = IGChatService(acc)
        try:
            svc.login_manual(password=password, verification_code=code)
            out = CompanyIGAccountOutSerializer(acc).data
            return Response({"status":"ok","login":"manual","account": out}, status=200)
        except TwoFactorRequired:
            return Response({"detail":"two_factor_required","account_id": str(acc.id)}, status=401)
        except ChallengeRequired:
            return Response({"detail":"checkpoint_challenge_required"}, status=401)
        except PleaseWaitFewMinutes as e:
            return Response({"detail": str(e)}, status=429)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)


class IGAccountLoginView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request, pk):
        if not (request.user.is_staff or request.user.is_superuser):
            account = get_company_account_or_404(request, pk)
        else:
            account = get_object_or_404(CompanyIGAccount, pk=pk, is_active=True)

        svc = IGChatService(account)

        if svc.try_resume_session():
            return Response({"login": "resumed"}, status=200)

        password = request.data.get("password")
        code = request.data.get("code")
        if not password:
            return Response({"detail": "manual_login_required"}, status=401)
        try:
            svc.login_manual(password=password, verification_code=code)
            return Response({"login": "manual"}, status=200)
        except TwoFactorRequired:
            return Response({"detail": "two_factor_required"}, status=401)
        except ChallengeRequired:
            return Response({"detail": "checkpoint_challenge_required"}, status=401)
        except PleaseWaitFewMinutes as e:
            return Response({"detail": str(e)}, status=429)
        except Exception as e:
            return Response({"detail": str(e)}, status=400)


class AutoLoginMyCompanyView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        from .autologin import autologin_company
        company_id = getattr(request.user, "company_id", None)
        if not company_id:
            return Response({"detail": "user_has_no_company"}, status=400)
        data = autologin_company(str(company_id))
        return Response({"status": "ok", **data})



class ThreadsLiveView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        # company-scope: обычный пользователь видит только свои аккаунты
        if not (request.user.is_staff or request.user.is_superuser):
            account = get_company_account_or_404(request, pk)
        else:
            account = get_object_or_404(CompanyIGAccount, pk=pk, is_active=True)

        svc = IGChatService(account)
        if not svc.try_resume_session():
            return Response({"detail": "manual_login_required"}, status=401)

        amount = int(request.query_params.get("amount", 20))
        return Response({"threads": svc.fetch_threads_live(amount=amount)})