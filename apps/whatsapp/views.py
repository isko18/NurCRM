import os
import subprocess
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import generics
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import WhatsAppSession, Message
from .serializers import MessageSerializer, SessionSerializer
from apps.users.models import Company


NODE_BIN = "node"  # путь к node
BAILEYS_SCRIPT = os.path.join(os.path.dirname(__file__), "baileys.js")


def call_baileys(args: list):
    try:
        subprocess.Popen(
            [NODE_BIN, BAILEYS_SCRIPT] + args,
            cwd=os.path.dirname(BAILEYS_SCRIPT)
        )
        return True
    except Exception as e:
        print("Ошибка запуска Node.js:", e)
        return False


# === API ===
class StartSession(APIView):
    def post(self, request, company_id):
        company = Company.objects.get(id=company_id)
        session, _ = WhatsAppSession.objects.get_or_create(company=company)
        call_baileys([str(company_id), "start"])
        return Response({"status": "started"})


class SendText(APIView):
    def post(self, request, company_id):
        phone = request.data.get("phone")
        text = request.data.get("text")
        if not (phone and text):
            return Response({"error": "phone и text обязательны"}, status=400)

        if call_baileys([str(company_id), "sendText", phone, text]):
            return Response({"status": "ok"})
        return Response({"error": "Не удалось запустить Node.js"}, status=500)


class SendMedia(APIView):
    def post(self, request, company_id):
        phone = request.data.get("phone")
        media_type = request.data.get("type")  # image, video, audio, document
        url = request.data.get("url")
        caption = request.data.get("caption", "")
        if not (phone and media_type and url):
            return Response({"error": "phone, type, url обязательны"}, status=400)

        if call_baileys([str(company_id), "sendMedia", phone, media_type, url, caption]):
            return Response({"status": "ok"})
        return Response({"error": "Не удалось запустить Node.js"}, status=500)


class SessionDetail(generics.RetrieveAPIView):
    queryset = WhatsAppSession.objects.all()
    serializer_class = SessionSerializer
    lookup_field = "company_id"


class MessageList(generics.ListAPIView):
    serializer_class = MessageSerializer

    def get_queryset(self):
        return Message.objects.filter(company_id=self.kwargs["company_id"]).order_by("-ts")


# === Webhooks от Node.js ===
class QRWebhook(APIView):
    def post(self, request):
        company_id = request.data["company_id"]
        qr = request.data["qr"]

        session = WhatsAppSession.objects.get(company_id=company_id)
        session.last_qr = qr
        session.is_ready = False
        session.save()

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"wa_{company_id}", {"type": "wa_qr", "qr": qr}
        )
        return Response({"ok": True})


class StatusWebhook(APIView):
    def post(self, request):
        company_id = request.data["company_id"]
        status_value = request.data["status"]

        session = WhatsAppSession.objects.get(company_id=company_id)
        session.is_ready = status_value == "open"
        session.save()

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"wa_{company_id}", {"type": "wa_status", "status": status_value}
        )
        return Response({"ok": True})


class MessageWebhook(APIView):
    def post(self, request):
        company_id = request.data["company_id"]
        phone = request.data["phone"]
        text = request.data.get("text")
        direction = request.data.get("direction", "in")
        msg_type = request.data.get("type", "text")
        caption = request.data.get("caption")

        company = Company.objects.get(id=company_id)
        msg = Message.objects.create(
            company=company,
            phone=phone,
            text=text,
            type=msg_type,
            caption=caption,
            direction=direction
        )

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"wa_{company_id}",
            {
                "type": "wa_message",
                "id": str(msg.id),
                "phone": phone,
                "text": text,
                "caption": caption,
                "direction": direction,
                "ts": msg.ts.isoformat(),
            },
        )
        return Response({"ok": True})
