import shutil
import traceback

try:
    src = r"c:\Users\Binh\.gemini\antigravity\brain\db2c0edc-26de-4985-b2ca-4bbfdf50cd7f\media__1775498667691.png"
    dst = r"c:\Users\Binh\Downloads\tro_gia_project\tro_gia_project\static\images\qr_thanh_toan.png"
    shutil.copy(src, dst)
    print("Copied successfully")
except Exception as e:
    print("Error:", str(e))
    traceback.print_exc()
