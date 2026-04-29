from sqlalchemy import create_engine, text
import os
import sys

engine = create_engine("mysql+pymysql://root:@127.0.0.1:3306/boarding_house")

with engine.connect() as conn:
    print("Connected to DB.")
    try:
        conn.execute(text("ALTER TABLE payments DROP CHECK ck_payments_status"))
        print("Dropped via DROP CHECK")
    except Exception as e:
        print("DROP CHECK failed:", e)
        try:
            conn.execute(text("ALTER TABLE payments DROP CONSTRAINT ck_payments_status"))
            print("Dropped via DROP CONSTRAINT")
        except Exception as e2:
            print("DROP CONSTRAINT failed:", e2)

    try:
        conn.execute(text("ALTER TABLE payments ADD CONSTRAINT ck_payments_status CHECK (status in ('paid', 'unpaid', 'overdue', 'pending_verification'))"))
        print("Added new constraint.")
    except Exception as e:
        print("Add constraint failed:", e)
    
    try:
        conn.execute(text("ALTER TABLE audit_logs DROP CHECK ck_audit_action"))
        print("Dropped audit via DROP CHECK")
    except Exception as e:
        try:
            conn.execute(text("ALTER TABLE audit_logs DROP CONSTRAINT ck_audit_action"))
            print("Dropped audit via DROP CONSTRAINT")
        except Exception as e2:
            pass

    try:
        conn.execute(text("ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_action CHECK (action in ('insert', 'update', 'delete', 'payment', 'payment_pending'))"))
    except Exception as e:
        print("Add audit constraint failed:", e)
    
    conn.commit()
    print("Done")
