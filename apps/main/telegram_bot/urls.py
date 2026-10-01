from django.urls import path
from apps.main.telegram_bot.views import (
    TelegramBotSettingsView,
    TelegramBotDetectOwnerChatView,
    TelegramBotTestMessageView,
    TelegramBotTestAIView,
    TelegramBotStatsView,
    TelegramBotInquiriesView,
    TelegramBotCustomersView,
    TelegramBotNotifyShiftClosedView,
)

urlpatterns = [
    path("settings/", TelegramBotSettingsView.as_view(), name="telegram-bot-settings"),
    path("detect-owner-chat/", TelegramBotDetectOwnerChatView.as_view(), name="telegram-bot-detect-owner-chat"),
    path("test-message/", TelegramBotTestMessageView.as_view(), name="telegram-bot-test-message"),
    path("test-ai/", TelegramBotTestAIView.as_view(), name="telegram-bot-test-ai"),
    path("stats/", TelegramBotStatsView.as_view(), name="telegram-bot-stats"),
    path("inquiries/", TelegramBotInquiriesView.as_view(), name="telegram-bot-inquiries"),
    path("customers/", TelegramBotCustomersView.as_view(), name="telegram-bot-customers"),
    path("notify/shift-closed/", TelegramBotNotifyShiftClosedView.as_view(), name="telegram-bot-notify-shift-closed"),
]
