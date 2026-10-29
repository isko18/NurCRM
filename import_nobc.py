from decimal import Decimal
from openpyxl import load_workbook
from django.db import transaction
from apps.main.models import Product
from apps.users.models import Company

CID="2b4e9bf1-bf05-473f-8f07-ca6bed66ee3a"
company=Company.objects.get(id=CID)
wb=load_workbook("/home/nur/beauty_hub_import.xlsx", read_only=True)
ws=wb.active
rows=list(ws.values)
hdr=rows[0]
def D(v):
    try: return Decimal(str(v).replace(",",".")) if v not in (None,"") else Decimal("0")
    except Exception: return Decimal("0")

todo=[r for r in rows[1:] if not (r[1] or "")]
print("строк без ШК:", len(todo))
existing={p.name.strip().casefold() for p in Product.objects.filter(company=company)}
created=0; skipped=0
with transaction.atomic():
    for r in todo:
        name=(r[0] or "").strip()
        if not name: continue
        if name.casefold() in existing:
            skipped+=1; continue
        p=Product(company=company, name=name,
                  quantity=D(r[2]), purchase_price=D(r[3]), price=D(r[4]),
                  unit=(r[5] or "шт"), article=(str(r[6]).strip() if r[6] else ""))
        p.save()
        existing.add(name.casefold()); created+=1
print("создано:", created, "| пропущено (уже есть по имени):", skipped)
print("итого товаров в компании:", Product.objects.filter(company=company).count())
