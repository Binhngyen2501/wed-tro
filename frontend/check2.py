from pyflakes.api import checkPath
from pyflakes.reporter import Reporter
import sys

class MyReporter(Reporter):
    def __init__(self, warningStream, errorStream):
        self.warnings = []
        super().__init__(warningStream, errorStream)
    
    def flake(self, message):
        self.warnings.append(str(message))

def run():
    with open('flake8_out2.txt', 'w', encoding='utf-8') as f:
        rep = Reporter(f, f)
        checkPath('app.py', rep)

if __name__ == '__main__':
    run()
