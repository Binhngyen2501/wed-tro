import shutil

src = r"c:\Users\Binh\.gemini\antigravity\brain\db2c0edc-26de-4985-b2ca-4bbfdf50cd7f\media__1775498667691.png"
dst = r"c:\Users\Binh\Downloads\tro_gia_project\tro_gia_project\static\images\qr_thanh_toan.png"
with open("copy_log.txt", "w") as f:
    try:
        shutil.copyfile(src, dst)
        f.write("OK\n")
    except Exception as e:
        f.write(f"ERROR: {e}\n")
