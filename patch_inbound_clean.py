
filepath = '/home/nur/apps/consalting/serializers.py'
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    new_lines.append(line)
    if 'class InboundLeadConsaltingSerializer(serializers.ModelSerializer):' in line:
        new_lines.append("    external_id = serializers.CharField(required=False, allow_blank=True, allow_null=True, default='')\n")
        new_lines.append("    full_name = serializers.CharField(required=False, allow_blank=True, default='')\n")
        new_lines.append("    phone = serializers.CharField(required=False, allow_blank=True, default='')\n")
        new_lines.append("    source = serializers.CharField(required=False, allow_blank=True, default='manual')\n")
        new_lines.append("    message = serializers.CharField(required=False, allow_blank=True, default='')\n")

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Patch applied cleanly!')
