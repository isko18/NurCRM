
filepath = '/home/nur/apps/consalting/serializers.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Fix InboundLeadConsaltingSerializer if missing fields
if 'class InboundLeadConsaltingSerializer(serializers.ModelSerializer):' in content:
    if 'external_id = serializers.CharField(required=False' not in content:
        content = content.replace(
            'class InboundLeadConsaltingSerializer(serializers.ModelSerializer):',
            '''class InboundLeadConsaltingSerializer(serializers.ModelSerializer):
    external_id = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')
    full_name = serializers.CharField(required=False, allow_blank=True, default='')
    phone = serializers.CharField(required=False, allow_blank=True, default='')
    source = serializers.CharField(required=False, allow_blank=True, default='manual')
    message = serializers.CharField(required=False, allow_blank=True, default='')'''
        )

# 2. Append RegionalFunnelRouting classes if missing
if 'class RegionalFunnelRoutingConsaltingSerializer' not in content:
    regional_code = '''


# ==========================
# RegionalFunnelRouting (§4.3)
# ==========================
class RegionalFunnelRuleConsaltingSerializer(serializers.ModelSerializer):
    funnel_id = serializers.UUIDField(source="funnel.id", read_only=True)
    funnel_display = serializers.SerializerMethodField()
    region_label = serializers.SerializerMethodField()

    class Meta:
        model = RegionalFunnelRuleConsalting
        fields = (
            "id", "funnel_id", "funnel_display", "region_code", "region_label",
            "phone_prefixes", "wazzup_account_ids", "source_channels",
            "assign_role_ids", "assign_strategy", "order"
        )
        read_only_fields = ("id", "funnel_id", "funnel_display", "region_label")

    def get_funnel_display(self, obj):
        return obj.funnel.name if obj.funnel else ""

    def get_region_label(self, obj):
        from .funnel.regional_routing import REGION_LABELS
        return REGION_LABELS.get(obj.region_code, obj.region_code)


class RegionalFunnelRoutingConsaltingSerializer(serializers.ModelSerializer):
    default_funnel_id = serializers.UUIDField(source="default_funnel.id", allow_null=True, required=False)
    default_funnel_display = serializers.SerializerMethodField()
    rules = RegionalFunnelRuleConsaltingSerializer(many=True, read_only=True)

    class Meta:
        model = RegionalFunnelRoutingConsalting
        fields = (
            "id", "enabled", "fallback_strategy",
            "default_funnel_id", "default_funnel_display",
            "rules"
        )
        read_only_fields = ("id", "default_funnel_display", "rules")

    def get_default_funnel_display(self, obj):
        return obj.default_funnel.name if obj.default_funnel else ""
'''
    content += regional_code

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print('Successfully restored and patched serializers.py!')
