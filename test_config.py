
import sys, os
os.chdir("D:/code/anima")
sys.path.insert(0, ".")
from anima.config import get
print("port:", get("dashboard.port", 8420))
print("hostname:", get("machine.hostname", "?"))
print("CONFIG OK")
