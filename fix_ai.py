import re

# Read file
with open('tro_gia_project/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove AI menu items from admin options
content = content.replace(
    '"Audit Log",\n                "Quản lý User",\n                "Trợ lý AI",',
    '"Audit Log",\n                "Quản lý User",'
)

# Remove AI menu items from user options  
content = content.replace(
    '"Hóa đơn của tôi",\n                "Trợ lý AI",',
    '"Hóa đơn của tôi",'
)

# Remove AI assistant call from main
content = content.replace(
    'elif selected == "Trợ lý AI":\n            render_user_ai_assistant(user)\n\n\ndef render_admin_notifications',
    '\n\ndef render_admin_notifications'
)

# Write back
with open('tro_gia_project/app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done!")
