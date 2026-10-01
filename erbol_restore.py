import json
from django.core.management import call_command
from django.db import transaction
from django.db.models import signals

SRC = "/root/erbol_export.json"
DST = "/tmp/erbol_restore_patched.json"
NEW_EMAIL = "aicholpon.erkinovna@icloud.com"
UID = "3df745e4-b6af-410b-a688-4fd9c8fb8490"

d = json.load(open(SRC))
patched = 0
for o in d:
    if o["model"] == "users.user" and o["pk"] == UID:
        old = o["fields"]["email"]
        o["fields"]["email"] = NEW_EMAIL
        o["fields"]["is_active"] = True
        o["fields"]["deleted_at"] = None
        o["fields"]["deleted_by"] = None
        patched += 1
        print("  email: %s -> %s" % (old, NEW_EMAIL))
json.dump(d, open(DST, "w"), ensure_ascii=False)
print("  пропатчено записей: %d, объектов в фикстуре: %d" % (patched, len(d)))

# Глушим сигналы: loaddata пишет через save_base(raw=True), но обработчики
# флаг raw не проверяют — create_cashbox_for_company вставит дубль кассы
# и фикстура упадёт на uq_cashbox_name_global_per_company.
muted = {}
for name in ("pre_save", "post_save", "m2m_changed"):
    sig = getattr(signals, name)
    muted[name] = (sig, sig.receivers)
    print("  %s: отключено %d обработчиков" % (name, len(sig.receivers)))
    sig.receivers = []
    sig.sender_receivers_cache.clear()

try:
    with transaction.atomic():
        call_command("loaddata", DST, verbosity=1)
    print("  LOADDATA OK")
finally:
    for name, (sig, rec) in muted.items():
        sig.receivers = rec
        sig.sender_receivers_cache.clear()
    print("  сигналы возвращены")
