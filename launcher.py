import subprocess,os
os.chdir("D:/code/anima")
p=subprocess.Popen(["E:/codesupport/anaconda/envs/anima/python.exe","-m","anima","--headless"],cwd="D:/code/anima",stdout=open("D:/code/anima/data/logs/anima_stdout.log","w"),stderr=subprocess.STDOUT,creationflags=8)
print(f"PID:{p.pid}")
