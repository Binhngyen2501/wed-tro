import traceback
import sys
import io

try:
    import app
    with open("test_out.txt", "w", encoding="utf-8") as f:
        f.write("SUCCESS\n")
except Exception as e:
    with open("test_out.txt", "w", encoding="utf-8") as f:
        traceback.print_exc(file=f)
