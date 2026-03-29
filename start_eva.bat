@echo off
cd /d "D:/code/anima"
"E:/codesupport/anaconda/envs/anima/python.exe" -m anima --headless > data/logs/anima_stdout.log 2>&1
