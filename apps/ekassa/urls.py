from django.urls import path

from apps.ekassa import views

urlpatterns = [
    path("settings/", views.EkassaSettingsView.as_view(), name="ekassa-settings"),
    path("ping/", views.EkassaPingView.as_view(), name="ekassa-ping"),
    path("logout/", views.ekassa_logout, name="ekassa-logout"),
    path("pos/", views.ekassa_pos, name="ekassa-pos"),
    path("catalogue/", views.ekassa_catalogue, name="ekassa-catalogue"),
    path("shift/state/", views.ekassa_shift_state, name="ekassa-shift-state"),
    path("shift/open/", views.ekassa_shift_open, name="ekassa-shift-open"),
    path("shift/close/", views.ekassa_shift_close, name="ekassa-shift-close"),
    path("shift/control/", views.ekassa_shift_control, name="ekassa-shift-control"),
    path("cash-operation/", views.ekassa_cash_operation, name="ekassa-cash-operation"),
    path("receipt/", views.ekassa_receipt, name="ekassa-receipt"),
    path("duplicate/", views.ekassa_duplicate, name="ekassa-duplicate"),
    path("customers/", views.ekassa_customers, name="ekassa-customers"),
    path("xpay/getqr/", views.ekassa_xpay_getqr, name="ekassa-xpay-getqr"),
    path("xpay/status/", views.ekassa_xpay_status, name="ekassa-xpay-status"),
    path("info/tax-systems/", views.ekassa_info_tax_systems, name="ekassa-info-tax-systems"),
    path("info/tax-rates/", views.ekassa_info_tax_rates, name="ekassa-info-tax-rates"),
    path("info/gns-departments/", views.ekassa_info_gns_departments, name="ekassa-info-gns"),
    path(
        "info/entrepreneurship-objects/",
        views.ekassa_info_entrepreneurship_objects,
        name="ekassa-info-entrepreneurship",
    ),
    path(
        "info/calc-item-attributes/",
        views.ekassa_info_calc_item_attributes,
        name="ekassa-info-calc-item",
    ),
    path(
        "info/business-activities/",
        views.ekassa_info_business_activities,
        name="ekassa-info-business",
    ),
]
