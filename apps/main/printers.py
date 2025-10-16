# pos/printers.py
from datetime import datetime
from escpos.printer import Usb
import usb.core, usb.util

KNOWN_VENDORS = {0x04B8:"Epson", 0x0519:"Star", 0x1504:"Bixolon", 0x28E9:"XPrinter", 0x0DD4:"Citizen", 0x0FE6:"GenericPOS"}

def _get_str(dev, idx):
    try:
        return usb.util.get_string(dev, idx) if idx else ""
    except Exception:
        return ""

def find_escpos_usb():
    best = None
    for dev in usb.core.find(find_all=True):
        try:
            cfg = dev.get_active_configuration()
        except Exception:
            continue
        score = 0
        if dev.idVendor in KNOWN_VENDORS: score += 1
        chosen = None
        for intf in cfg:
            out_ep, in_ep = None, None
            for ep in intf.endpoints():
                if usb.util.endpoint_type(ep.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK:
                    if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
                        out_ep = ep.bEndpointAddress
                    if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                        in_ep = ep.bEndpointAddress
            if out_ep is not None:
                score += 2 if intf.bInterfaceClass == 0x07 else 1
                chosen = {
                    "vid": dev.idVendor, "pid": dev.idProduct,
                    "interface": intf.bInterfaceNumber,
                    "out_ep": out_ep, "in_ep": in_ep,
                    "label": f"{_get_str(dev, dev.iManufacturer) or KNOWN_VENDORS.get(dev.idVendor,'')} {_get_str(dev, dev.iProduct) or ''}".strip()
                }
        if chosen:
            best = chosen if (best is None or score > best.get("_score", 0)) else best
            if best is chosen:
                best["_score"] = score
    if not best:
        raise RuntimeError("ESC/POS USB принтер не найден")
    return best

class UsbEscposPrinter:
    def __init__(self):
        d = find_escpos_usb()
        self.dev_info = d
        self.prn = Usb(d["vid"], d["pid"], interface=d["interface"], out_ep=d["out_ep"], in_ep=d["in_ep"], timeout=10)

    def print_sale(self, sale, doc_no: str, fmt_money):
        p = self.prn
        p.set(align="center", bold=True, width=1, height=1); p.text("ЧЕК ПРОДАЖИ\n")
        p.set(align="left", bold=False, width=1, height=1)
        p.text(f"№ {doc_no}\n{sale.created_at:%d.%m.%Y %H:%M}\n")
        p.text("-"*32 + "\n")
        for it in sale.items.all():
            name = (it.name_snapshot or "").strip()
            name1, name2 = name[:22], name[22:44]
            qty = it.quantity or 0
            price = it.unit_price or 0
            total = (price * qty) if (price and qty) else 0
            p.text(f"{name1}\n")
            if name2: p.text(f"{name2}\n")
            left = f"{qty} x {fmt_money(price)}"
            spaces = max(1, 28 - len(left))
            p.text(left + " "*spaces + f"{fmt_money(total)}\n")
        p.text("-"*32 + "\n")
        p.text(f"СУММА: {fmt_money(sale.subtotal)}\n")
        if sale.discount_total and sale.discount_total > 0: p.text(f"СКИДКА: {fmt_money(sale.discount_total)}\n")
        if sale.tax_total and sale.tax_total > 0: p.text(f"НАЛОГ: {fmt_money(sale.tax_total)}\n")
        p.set(bold=True); p.text(f"ИТОГО: {fmt_money(sale.total)}\n"); p.set(bold=False)
        # QR (если захочешь, можно использовать p.qr(...); оставил простую строку)
        p.text("\nСПАСИБО ЗА ПОКУПКУ!\n")
        p.cut()
        p.close()
        return {"ok": True, "device": self.dev_info.get("label")}
