"""ТЗ часть 15: ИИ и голос в Telegram-боте на сервере (голос, сотрудники, архив смен, действия с подтверждением, накладная по фото, напоминания должникам)."""
import uuid
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase
from django.utils import timezone

from apps.construction.models import CashShift
from apps.main.models import Product, StockMovement
from apps.main.telegram_bot.models import TelegramBotSettings
from apps.main.telegram_bot.services import ai_service, owner_actions
from apps.main.telegram_bot.services.ai_analytics_functions import (
    AI_TOOL_DECLARATIONS,
    OWNER_ACTION_TOOL_DECLARATIONS,
    fn_get_expiring,
    fn_get_shift_archive,
    fn_get_staff,
    fn_get_stock_alerts,
)
from apps.main.telegram_bot.tasks import process_telegram_update
from apps.main.tests_kassa_api import KassaBase

OWNER_CHAT = "777"
TG = "apps.main.telegram_bot.services.telegram_api."
AI = "apps.main.telegram_bot.services.ai_service."


def _update(message: dict, chat_id: str = OWNER_CHAT) -> dict:
    return {
        "update_id": int(uuid.uuid4().int % 2_000_000_000),
        "message": {
            "message_id": 1, "date": int(timezone.now().timestamp()),
            "chat": {"id": int(chat_id)}, "from": {"id": int(chat_id), "first_name": "Owner"},
            **message,
        },
    }


class Part15Base(KassaBase):
    def setUp(self):
        super().setUp()
        self.bot = TelegramBotSettings.objects.create(
            company=self.company, mode=TelegramBotSettings.Mode.SERVER, token="123456:TEST",
            owner_chat_id=OWNER_CHAT, ai_key="test-gemini-key",
        )
        self.mars = Product.objects.create(
            company=self.company, name="Батончик Mars 50г", barcode="4011100091108",
            purchase_price=Decimal("50"), markup_percent=Decimal("20"), price=Decimal("60"), quantity=Decimal("10"),
        )
        self.tg = {}
        for name in ("send_message", "send_voice", "send_chat_action", "edit_message_reply_markup", "answer_callback_query"):
            p = patch(TG + name, return_value={"ok": True, "result": {"message_id": 5}})
            self.tg[name] = p.start()
            self.addCleanup(p.stop)

    def _sent_texts(self):
        return [c.args[2] for c in self.tg["send_message"].call_args_list]


class CapabilitiesAndSettingsTests(Part15Base):
    def test_capabilities(self):
        r = self.api.get("/api/main/telegram-bot/capabilities/")
        self.assertEqual(r.status_code, 200, r.data)
        for k in ("voice_in", "voice_out", "staff", "shift_archive", "product_actions", "invoice_photo", "debt_reminders"):
            self.assertTrue(r.data[k], k)

    def test_settings_new_fields(self):
        r = self.api.get("/api/main/telegram-bot/settings/")
        self.assertEqual((r.data["voice_language"], str(r.data["ai_min_markup_percent"]), r.data["ai_owner_actions_enabled"]), ("auto", "20.00", True))
        r = self.api.patch("/api/main/telegram-bot/settings/", {"voice_language": "ky", "ai_min_markup_percent": "25"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.bot.refresh_from_db()
        self.assertEqual((self.bot.voice_language, self.bot.ai_min_markup_percent), ("ky", Decimal("25")))


class VoiceTests(Part15Base):
    def _voice_update(self, kind="voice"):
        part = {"file_id": "f1", "mime_type": "audio/ogg"} if kind == "voice" else {"file_id": "f1"}
        return _update({kind: part})

    @patch(AI + "synthesize_voice", return_value=b"OGGDATA")
    @patch(AI + "generate_chat_response_with_tools")
    @patch(AI + "transcribe_voice", return_value="как прошла неделя")
    @patch(TG + "download_file", return_value=b"audio")
    @patch(TG + "get_file", return_value={"file_path": "voice/1.oga"})
    def test_voice_in_voice_out(self, _gf, _dl, _tr, mock_ai, mock_tts):
        mock_ai.return_value = (
            "[ГОЛОС] Выручка за неделю около тысячи сом, десять чеков. [ТЕКСТ] • Пн — 500 сом\n• Вт — 500 сом", "m", [],
        )
        process_telegram_update(str(self.bot.id), self._voice_update())
        # ИИ получил распознанный текст и голосовой режим
        kwargs = mock_ai.call_args.kwargs
        self.assertEqual(kwargs["contents"][-1]["parts"][0]["text"], "как прошла неделя")
        self.assertIn("[ГОЛОС]", kwargs["system_instruction"])
        self.assertIn("я текстовый бот", kwargs["system_instruction"])
        # индикатор записи, голосовой ответ и подробности текстом
        self.tg["send_chat_action"].assert_called_with("123456:TEST", OWNER_CHAT, "record_voice")
        self.assertEqual(mock_tts.call_args.args[1], "Выручка за неделю около тысячи сом, десять чеков.")
        self.tg["send_voice"].assert_called_once()
        self.assertIn("Пн — 500 сом", self._sent_texts()[-1])

    @patch(AI + "transcribe_voice", return_value="")
    @patch(TG + "download_file", return_value=b"audio")
    @patch(TG + "get_file", return_value={"file_path": "voice/1.oga"})
    def test_unrecognized_voice(self, *_):
        process_telegram_update(str(self.bot.id), self._voice_update("video_note"))
        self.assertEqual(self._sent_texts(), ["Не расслышал, повторите или напишите текстом."])

    @patch(AI + "synthesize_voice", return_value=b"OGG")
    @patch(AI + "generate_chat_response_with_tools", return_value=("Ответ текстом.", "m", []))
    @patch(AI + "transcribe_voice", return_value="сколько должников")
    @patch(TG + "download_file", return_value=b"audio")
    @patch(TG + "get_file", return_value={"file_path": "a.mp3"})
    def test_voice_replies_disabled_answers_text(self, *_):
        TelegramBotSettings.objects.filter(pk=self.bot.pk).update(voice_replies_enabled=False)
        process_telegram_update(str(self.bot.id), self._voice_update("audio"))
        self.tg["send_voice"].assert_not_called()
        self.assertIn("Ответ текстом.", self._sent_texts()[-1])

    def test_helpers(self):
        self.assertEqual(ai_service.detect_language("бүгүн канча сатылды"), "ky")
        self.assertEqual(ai_service.detect_language("сколько продали сегодня"), "ru")
        v, t = ai_service.split_voice_and_text("[ГОЛОС] Коротко. [ТЕКСТ] <b>Детали</b>")
        self.assertEqual((v, t), ("Коротко.", "<b>Детали</b>"))
        v, t = ai_service.split_voice_and_text("Первое. Второе. Третье предложение длиннее. Четвёртое.")
        self.assertEqual(v, "Первое. Второе.")
        self.assertTrue(t)


class ProductActionTests(Part15Base):
    def _ai_proposes(self, changes):
        """ИИ вызывает propose_product_changes и отвечает текстом."""
        def fake(api_key, system_instruction, contents, tools, execute_tool_fn, **kw):
            res = execute_tool_fn("propose_product_changes", {"changes": changes})
            self.assertTrue(res.get("pending"), res)
            return "Спишу 1 Mars. Подтвердите.", "m", ["propose_product_changes"]
        return fake

    def test_writeoff_requires_confirmation_and_runs_on_button(self):
        with patch(AI + "generate_chat_response_with_tools", side_effect=self._ai_proposes(
            [{"action": "writeoff", "product": "Марс", "qty": 1, "reason": "просрочка"}]
        )):
            process_telegram_update(str(self.bot.id), _update({"text": "Спиши 1 Марс, просрочка"}))
        self.mars.refresh_from_db()
        self.assertEqual(self.mars.quantity, Decimal("10"))  # до нажатия ничего не меняется
        confirm_call = self.tg["send_message"].call_args_list[-1]
        self.assertIn("Списание: Батончик Mars 50г −1 шт — просрочка", confirm_call.args[2])
        buttons = confirm_call.kwargs["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in buttons], ["✅ Выполнить", "❌ Отмена"])
        run_data = buttons[0]["callback_data"]

        process_telegram_update(str(self.bot.id), {
            "update_id": 999001,
            "callback_query": {"id": "cq1", "from": {"id": int(OWNER_CHAT)}, "data": run_data,
                               "message": {"message_id": 5, "chat": {"id": int(OWNER_CHAT)}}},
        })
        self.mars.refresh_from_db()
        self.assertEqual(self.mars.quantity, Decimal("9"))
        mv = StockMovement.objects.get(object_id=self.mars.id, type=StockMovement.Type.WRITEOFF)
        self.assertEqual((mv.qty_before, mv.change, mv.qty_after), (Decimal("10"), Decimal("-1"), Decimal("9")))
        self.assertIn("Telegram-бот (ИИ), подтвердил владелец", mv.comment)
        self.assertIn("Выполнено", self._sent_texts()[-1])
        self.assertIsNone(owner_actions.get_pending(self.company.id, OWNER_CHAT))

    def test_text_yes_and_no(self):
        with patch(AI + "generate_chat_response_with_tools", side_effect=self._ai_proposes(
            [{"action": "update", "product": "Mars", "field": "price", "value": "75"},
             {"action": "add", "product": "4011100091108", "qty": 5, "purchase_price": 55}]
        )):
            process_telegram_update(str(self.bot.id), _update({"text": "поставь цену Mars 75 и приход 5 по 55"}))
        process_telegram_update(str(self.bot.id), _update({"text": "нет"}))
        self.assertIn("Отменено", self._sent_texts()[-1])
        self.mars.refresh_from_db()
        self.assertEqual(self.mars.price, Decimal("60"))

        with patch(AI + "generate_chat_response_with_tools", side_effect=self._ai_proposes(
            [{"action": "update", "product": "Mars", "field": "price", "value": "75"},
             {"action": "add", "product": "4011100091108", "qty": 5, "purchase_price": 55}]
        )):
            process_telegram_update(str(self.bot.id), _update({"text": "поставь цену Mars 75 и приход 5 по 55"}))
        process_telegram_update(str(self.bot.id), _update({"text": "да"}))
        self.mars.refresh_from_db()
        self.assertEqual((self.mars.price, self.mars.quantity, self.mars.purchase_price), (Decimal("75"), Decimal("15"), Decimal("55")))
        self.assertTrue(StockMovement.objects.filter(object_id=self.mars.id, type=StockMovement.Type.INCOME, change=Decimal("5")).exists())

    def test_actions_disabled_removes_tool(self):
        TelegramBotSettings.objects.filter(pk=self.bot.pk).update(ai_owner_actions_enabled=False)
        with patch(AI + "generate_chat_response_with_tools", return_value=("ок", "m", [])) as m:
            process_telegram_update(str(self.bot.id), _update({"text": "спиши 1 Марс"}))
        self.assertNotIn("propose_product_changes", [t["name"] for t in m.call_args.kwargs["tools"]])

    def test_not_found_product(self):
        res = owner_actions.normalize_changes(self.company, [{"action": "writeoff", "product": "Несуществующий", "qty": 1}])
        self.assertEqual((res[0], res[1]), ([], ["Несуществующий"]))


class InvoicePhotoTests(Part15Base):
    ROWS = {"rows": [
        {"name": "Батончик Mars 50г", "qty": 10, "unit_price": 50},
        {"name": "Snickers 50г", "qty": 20, "unit_price": None, "line_total": 800, "barcode": "5000159461122"},
    ], "unparsed": ["??? 3 шт"]}

    @patch(TG + "download_file", return_value=b"jpegbytes")
    @patch(TG + "get_file", return_value={"file_path": "photos/1.jpg"})
    def test_invoice_flow(self, *_):
        with patch(AI + "generate_json", return_value=self.ROWS):
            process_telegram_update(str(self.bot.id), _update({"photo": [{"file_id": "s", "file_size": 10}, {"file_id": "l", "file_size": 99}]}))
        texts = self._sent_texts()
        self.assertIn("Наценка 20 %", texts[-2])
        confirm = texts[-1]
        self.assertIn("Приход: Батончик Mars 50г +10 шт (закупка 50, цена 60)", confirm)
        self.assertIn("Новый товар: Snickers 50г, ШК 5000159461122 — 20 шт, закупка 40, цена 48", confirm)
        self.assertIn("??? 3 шт", confirm)

        process_telegram_update(str(self.bot.id), _update({"text": "поставь 25 %"}))
        confirm2 = self._sent_texts()[-1]
        self.assertIn("наценка 25 %", confirm2)
        self.assertIn("цена 62.5", confirm2)
        self.assertIn("цена 50", confirm2)

        process_telegram_update(str(self.bot.id), _update({"text": "выполнить"}))
        self.mars.refresh_from_db()
        self.assertEqual((self.mars.quantity, self.mars.purchase_price, self.mars.price), (Decimal("20"), Decimal("50"), Decimal("62.50")))
        new = Product.objects.get(company=self.company, barcode="5000159461122")
        self.assertEqual((new.quantity, new.purchase_price, new.price), (Decimal("20"), Decimal("40"), Decimal("50")))
        self.assertTrue(StockMovement.objects.filter(object_id=new.id, type=StockMovement.Type.INCOME).exists())

    @patch(TG + "download_file", return_value=b"jpegbytes")
    @patch(TG + "get_file", return_value={"file_path": "photos/1.jpg"})
    def test_unreadable_photo(self, *_):
        with patch(AI + "generate_json", return_value=None):
            process_telegram_update(str(self.bot.id), _update({"photo": [{"file_id": "l", "file_size": 99}]}))
        self.assertIn("Не смог разобрать", self._sent_texts()[-1])


class DebtReminderTests(Part15Base):
    def test_whatsapp_links(self):
        debtors = {"total_debt": "0", "debtors": [
            {"name": "Айбек", "phone": "0700 123 456", "debt_amount": "221507.00", "oldest_debt_date": "2026-09-24"},
            {"name": "Асель", "phone": "", "debt_amount": "300.00", "oldest_debt_date": None},
        ]}
        with patch("apps.main.telegram_bot.services.ai_analytics_functions.fn_get_debtors", return_value=debtors):
            process_telegram_update(str(self.bot.id), _update({"text": "Напомни должникам"}))
        text = self._sent_texts()[-1]
        self.assertIn("https://wa.me/996700123456?text=", text)
        self.assertIn("221%20507", text)
        self.assertIn("%D0%97%D0%B4%D1%80%D0%B0%D0%B2%D1%81%D1%82%D0%B2%D1%83%D0%B9%D1%82%D0%B5%2C%20%D0%90%D0%B9%D0%B1%D0%B5%D0%BA", text)
        self.assertIn("Асель — 300 сом — нет телефона", text)
        self.assertIn("Итого:</b> 221 807 сом", text)
        self.assertIn("с 24.09", text)

    def test_phone_normalization(self):
        self.assertEqual(owner_actions.normalize_kg_phone("+996 (555) 00-02-22"), "996555000222")
        self.assertEqual(owner_actions.normalize_kg_phone("0555000222"), "996555000222")
        self.assertEqual(owner_actions.normalize_kg_phone("abc"), "")


class OwnerDataFunctionsTests(Part15Base):
    def test_staff_and_shift_archive(self):
        r = self.quick()
        self.assertEqual(r.status_code, 201, r.data)
        CashShift.objects.filter(pk=self.shift.pk).update(
            status=CashShift.Status.CLOSED, closed_at=timezone.now() + timezone.timedelta(hours=8),
            closing_cash=Decimal("1200"), sales_total=Decimal("200"), cash_sales_total=Decimal("200"), sales_count=1,
        )
        staff = fn_get_staff(self.company)
        me = next(e for e in staff["employees"] if (e.get("timesheet") or {}).get("shifts"))
        self.assertEqual((me["timesheet"]["shifts"], me["timesheet"]["days"]), (1, 1))
        self.assertGreaterEqual(me["timesheet"]["hours"], 7.9)
        arch = fn_get_shift_archive(self.company, limit=10)
        self.assertEqual(arch["closed_count"], 1)
        z = arch["closed_shifts"][0]
        self.assertEqual((z["number"], z["sales_cash"], z["opening_cash"], z["closing_cash"], z["checks"]), (1, "200.00", "1000.00", "1200.00", 1))

    def test_stock_alerts_and_expiring(self):
        self.assertEqual(self.quick().status_code, 201)
        Product.objects.filter(pk=self.product.pk).update(quantity=0)
        alerts = fn_get_stock_alerts(self.company)
        so = alerts["sold_out_bestsellers"][0]
        self.assertEqual((so["name"], so["sold_period"]), ("Хлеб", "2"))
        self.assertGreaterEqual(so["order_qty_14d"], 1)
        Product.objects.filter(pk=self.mars.pk).update(expiration_date=timezone.localdate() - timezone.timedelta(days=1))
        exp = fn_get_expiring(self.company)
        self.assertEqual((exp["expired_count"], exp["expired"][0]["name"]), (1, "Батончик Mars 50г"))

    def test_customer_never_gets_owner_tools(self):
        """П. 8.8: покупатель спрашивает про зарплату — функции владельца и действия ему недоступны."""
        owner_names = {t["name"] for t in AI_TOOL_DECLARATIONS} | {t["name"] for t in OWNER_ACTION_TOOL_DECLARATIONS}
        with patch(AI + "generate_owner_ai_response") as owner_ai, \
                patch(AI + "generate_chat_response", return_value=("Подскажу по товарам.", "m")) as cust_ai, \
                patch(AI + "generate_chat_response_with_tools", return_value=("Подскажу по товарам.", "m", [])) as cust_tools, \
                patch("apps.main.telegram_bot.services.ai_analytics_functions.fn_get_staff") as staff:
            process_telegram_update(str(self.bot.id), _update({"text": "Какая зарплата у сотрудников и кто должники?"}, chat_id="5551"))
        owner_ai.assert_not_called()
        staff.assert_not_called()
        for call in list(cust_ai.call_args_list) + list(cust_tools.call_args_list):
            tools = call.kwargs.get("tools") or []
            self.assertFalse({t.get("name") for t in tools} & owner_names, "покупателю ушли функции владельца")


class PureHelpersTests(SimpleTestCase):
    def test_markup_request(self):
        self.assertEqual(owner_actions.parse_markup_request("поставь 25 %"), Decimal("25"))
        self.assertEqual(owner_actions.parse_markup_request("наценка 30"), Decimal("30"))
        self.assertIsNone(owner_actions.parse_markup_request("спасибо"))

    def test_debtor_names(self):
        self.assertEqual(owner_actions.extract_debtor_names("напомни должникам"), [])
        self.assertEqual(owner_actions.extract_debtor_names("напомни должникам Айбеку и Асель"), ["айбеку", "асель"])
