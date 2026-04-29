import sys

try:
    with open('app.py', encoding='utf-8') as f:
        for i, l in enumerate(f):
            low = l.lower()
            if 'momo' in low or 'qr' in low or 'thanh' in low or 'toán' in low:
                print(f"{i+1}: {l.strip()}")
except Exception as e:
    print(e)
