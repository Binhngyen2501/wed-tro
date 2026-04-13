import sys
with open('app.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 'st.image' in line or 'use_column_width' in line:
            print(f'{i+1}: {line.strip()}')
