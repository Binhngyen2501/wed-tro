import json

lines = []
try:
    with open('app.py', 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            l_lower = line.lower()
            if 'qr' in l_lower or 'momo' in l_lower or 'thanh to' in l_lower or 'image' in l_lower:
                lines.append({"line": i, "content": line.strip()})
    with open('search.json', 'w', encoding='utf-8') as out:
        json.dump(lines, out, ensure_ascii=False, indent=2)
except Exception as e:
    with open('search.json', 'w', encoding='utf-8') as out:
        json.dump([{"error": str(e)}], out)
