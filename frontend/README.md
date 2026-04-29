# Hệ thống quản lý giá phòng trọ và gợi ý giá thuê

Project Python + Streamlit + SQLAlchemy bám theo báo cáo thực tập:
- Đăng ký / đăng nhập
- Phân quyền Admin / User
- Quản lý phòng
- Quản lý người thuê
- Quản lý hợp đồng
- Quản lý thanh toán
- Gợi ý giá thuê theo weighted scoring
- Audit log lưu vết thay đổi
- Xuất phiếu thu PDF

## 1) Cài đặt

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## 2) Cấu hình CSDL

Tạo file `.env` từ `.env.example`, sau đó sửa `DATABASE_URL` cho đúng MySQL của bạn.

Ví dụ MySQL:
```env
DATABASE_URL=mysql+pymysql://root:123456@localhost:3306/boarding_house
```

> Nếu bạn muốn chạy nhanh để demo, có thể tạm dùng SQLite:
```env
DATABASE_URL=sqlite:///boarding_house.db
```

## 3) Tạo database MySQL

```sql
CREATE DATABASE boarding_house CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Hoặc dùng file `sql/init_mysql.sql`.

## 4) Chạy ứng dụng

```bash
streamlit run app.py
```

## 5) Tài khoản mặc định

- Username: `admin`
- Password: `Admin@123`

Có thể đổi trong file `.env`.

## 6) Quy tắc nghiệp vụ đã xử lý

- Mỗi phòng chỉ có tối đa 1 hợp đồng `active`
- Khi tạo hợp đồng active -> phòng chuyển `occupied`
- Khi kết thúc hợp đồng -> nếu không còn hợp đồng active, phòng chuyển `available`
- Mỗi hợp đồng, mỗi kỳ chỉ có 1 payment `UNIQUE(contract_id, period)`
- Ghi audit log khi thêm/sửa/xóa dữ liệu trọng yếu

## 7) Cấu trúc project

```text
tro_gia_project/
├─ app.py
├─ auth.py
├─ db.py
├─ models.py
├─ requirements.txt
├─ services/
│  ├─ audit_service.py
│  ├─ billing_service.py
│  └─ pricing_service.py
├─ utils/
│  └─ validators.py
└─ sql/
   └─ init_mysql.sql
```
