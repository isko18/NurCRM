# pos/printers.py (USB только)
import usb.core, usb.util
from escpos.printer import Usb

KNOWN_VENDORS = {0x04B8:"Epson", 0x0519:"Star", 0x1504:"Bixolon", 0x28E9:"XPrinter", 0x0DD4:"Citizen", 0x0FE6:"GenericPOS"}

def _get_str(dev, idx):
    try:
        return usb.util.get_string(dev, idx) if idx else ""
    except Exception:
        return ""

def _list_candidates():
    cands = []
    for dev in usb.core.find(find_all=True):
        try: cfg = dev.get_active_configuration()
        except Exception: continue
        score = (1 if dev.idVendor in KNOWN_VENDORS else 0)
        for intf in cfg:
            out_ep = in_ep = None
            for ep in intf.endpoints():
                if usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK:
                    if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT: out_ep = ep.bEndpointAddress
                    else: in_ep = ep.bEndpointAddress
            if out_ep is not None:
                score2 = score + (2 if intf.bInterfaceClass == 0x07 else 1)
                cands.append({
                    "vid": dev.idVendor, "pid": dev.idProduct,
                    "interface": intf.bInterfaceNumber,
                    "out_ep": out_ep, "in_ep": in_ep,
                    "label": f"{_get_str(dev, dev.iManufacturer) or KNOWN_VENDORS.get(dev.idVendor,'')} {_get_str(dev, dev.iProduct) or ''}".strip(),
                    "score": score2
                })
    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands

class UsbEscposPrinter:
    def __init__(self):
        c = (_list_candidates() or [])
        if not c:
            raise RuntimeError("ESC/POS USB принтер не найден (PyUSB не видит устройств)")
        d = c[0]  # лучший кандидат
        # попытка отцепить драйвер ядра (Linux)
        try:
            dev = usb.core.find(idVendor=d["vid"], idProduct=d["pid"])
            if dev and dev.is_kernel_driver_active(d["interface"]):
                try: dev.detach_kernel_driver(d["interface"])
                except Exception: pass
        except Exception:
            pass

        self.info = d
        self.p = Usb(d["vid"], d["pid"], interface=d["interface"], out_ep=d["out_ep"], in_ep=d["in_ep"], timeout=10)

    def print_sale(self, sale, doc_no: str, fmt_money):
        p = self.p
        p.set(align="center", bold=True); p.text("ЧЕК ПРОДАЖИ\n")
        p.set(align="left", bold=False)
        p.text(f"№ {doc_no}\n{sale.created_at:%d.%m.%Y %H:%M}\n")
        p.text("-"*32 + "\n")
        for it in sale.items.all():
            name = (it.name_snapshot or "").strip()
            name1, name2 = name[:22], name[22:44]
            qty, price = it.quantity or 0, it.unit_price or 0
            total = price * qty
            p.text(f"{name1}\n")
            if name2: p.text(f"{name2}\n")
            left = f"{qty} x {fmt_money(price)}"
            p.text(left + " " * max(1, 28 - len(left)) + f"{fmt_money(total)}\n")
        p.text("-"*32 + "\n")
        p.text(f"СУММА: {fmt_money(sale.subtotal)}\n")
        if sale.discount_total and sale.discount_total > 0: p.text(f"СКИДКА: {fmt_money(sale.discount_total)}\n")
        if sale.tax_total and sale.tax_total > 0: p.text(f"НАЛОГ: {fmt_money(sale.tax_total)}\n")
        p.set(bold=True); p.text(f"ИТОГО: {fmt_money(sale.total)}\n"); p.set(bold=False)
        p.text("\nСПАСИБО ЗА ПОКУПКУ!\n")
        p.cut(); p.close()
        return {"ok": True, "device": self.info.get("label")}
