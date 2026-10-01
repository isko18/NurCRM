import json
from django.apps import apps
from collections import Counter, defaultdict
d=json.load(open("/root/erbol_export.json"))
by=defaultdict(list)
for o in d: by[o["model"]].append(o)

print("=== 1. КОЛЛИЗИИ PK (объект уже есть в проде) ===")
bad=0
for model,objs in by.items():
    M=apps.get_model(*model.split("."))
    pks=[o["pk"] for o in objs]
    exist=set(str(x) for x in M._base_manager.filter(pk__in=pks).values_list("pk",flat=True))
    if exist:
        bad+=len(exist)
        print(f"  !! {model}: {len(exist)} из {len(pks)} уже существуют  напр. {list(exist)[:3]}")
print("  коллизий всего:", bad)

print()
print("=== 2. ГЛОБАЛЬНЫЕ СПРАВОЧНИКИ ===")
from apps.users.models import SubscriptionPlan, Industry, Sector
for M,pk,label in ((SubscriptionPlan,"98becafa-4233-4474-9f39-8d0c433b999b","plan"),
                   (Industry,"9dd0706b-fb3d-45b8-9490-037a0f79a83a","industry"),
                   (Sector,"e9f05beb-5e14-4153-b523-4e1600c9b2e0","sector")):
    o=M.objects.filter(pk=pk).first()
    print(f"  {label}: {'OK  '+str(o) if o else 'ОТСУТСТВУЕТ'}")

print()
print("=== 3. ДРЕЙФ СХЕМЫ: поля модели, которых нет в фикстуре ===")
for model,objs in sorted(by.items()):
    M=apps.get_model(*model.split("."))
    have=set(objs[0]["fields"].keys())
    missing=[]
    for f in M._meta.get_fields():
        if not getattr(f,"concrete",False) or f.primary_key: continue
        if f.name in have or getattr(f,"attname","") in have: continue
        nullable = getattr(f,"null",False)
        hasdef = f.has_default()
        blank_ok = nullable or hasdef
        missing.append(f"{f.name}{'' if blank_ok else '  <-- NOT NULL БЕЗ DEFAULT!'}")
    if missing:
        print(f"  {model}: {', '.join(missing)}")

print()
print("=== 4. email-коллизия ===")
from django.contrib.auth import get_user_model
U=get_user_model()
for e in ("aicholpon.erkinova@icloud.com","aicholpon.erkinovna@icloud.com"):
    u=U._base_manager.filter(email=e).first()
    print(f"  {e}: {'ЗАНЯТ -> '+str(u.id) if u else 'свободен'}")
