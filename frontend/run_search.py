import traceback
with open('result_search.txt', 'w', encoding='utf-8') as out:
    try:
        with open('app.py', 'r', encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                if 'qr' in line.lower() or 'momo' in line.lower() or 'image' in line.lower() or 'st.' in line:
                    out.write(f'{i}: {line.strip()}\n')
    except Exception as e:
        out.write(traceback.format_exc())
