
filepath = "/home/nur/apps/consalting/serializers.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

if "RegionalFunnelRoutingConsalting," not in content:
    content = content.replace(
        "SaleRefundConsalting,",
        "SaleRefundConsalting,\n    RegionalFunnelRoutingConsalting,\n    RegionalFunnelRuleConsalting,"
    )

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

print("Updated imports in serializers.py!")
