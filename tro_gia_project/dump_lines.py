with open("dump_lines.txt", "w", encoding="utf-8") as out:
    with open("app.py", "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if "write_audit_log" in line or "serialize_model" in line:
                out.write(f"{i}: {line.strip()}\n")
