import subprocess
import sys

def run():
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "flake8"])
    except Exception:
        pass

    with open('flake8_output.txt', 'w', encoding='utf-8') as f:
        subprocess.call([sys.executable, "-m", "flake8", "app.py"], stdout=f, stderr=f)

if __name__ == '__main__':
    run()
