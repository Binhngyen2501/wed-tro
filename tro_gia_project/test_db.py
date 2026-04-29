import sys
import socket

# Test 1: Port 3306 có mở không?
print("=== Test kết nối MySQL ===")
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
result = s.connect_ex(('127.0.0.1', 3306))
s.close()
if result == 0:
    print("[OK] Port 3306 đang MỞ - MySQL đang chạy")
else:
    print(f"[FAIL] Port 3306 ĐÓNG (code={result}) - MySQL CHƯA chạy")
    sys.exit(1)

# Test 2: Kết nối pymysql
try:
    import pymysql
    conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password='', connect_timeout=3)
    print("[OK] Kết nối MySQL thành công (không password)")
    cur = conn.cursor()
    cur.execute("SHOW DATABASES;")
    dbs = [r[0] for r in cur.fetchall()]
    print(f"[INFO] Databases: {dbs}")
    if 'boarding_house' in dbs:
        print("[OK] Database 'boarding_house' đã tồn tại")
    else:
        print("[WARN] Database 'boarding_house' CHƯA tồn tại - cần tạo mới")
    conn.close()
except Exception as e:
    print(f"[FAIL] Lỗi kết nối: {e}")
    sys.exit(1)
