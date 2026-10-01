
filepath = '/home/nur/apps/consalting/serializers.py'
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
inserted = False
for line in lines:
    new_lines.append(line)
    if 'class InboundLeadConsaltingSerializer' in line:
        inserted = False
    if 'read_only_fields = (' in line and not inserted:
        pass
    if '"created_at", "updated_at"' in line and not inserted:
        pass
    if ')' in line and 'created_at' in ''.join(new_lines[-10:]) and not inserted:
        extra = '''        extra_kwargs = {
            "external_id": {"required": False, "allow_blank": True, "allow_null": True, "default": ""},
            "full_name": {"required": False, "allow_blank": True, "default": ""},
            "phone": {"required": False, "allow_blank": True, "default": ""},
            "source": {"required": False, "allow_blank": True, "default": "manual"},
            "message": {"required": False, "allow_blank": True, "default": ""},
        }\n'''
        new_lines.append(extra)
        inserted = True

with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Patch applied successfully. Inserted:', inserted)
