import sys, paramiko
sys.stdout.reconfigure(encoding="utf-8")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("100.109.112.90", username="29502", password="***REDACTED***",
            timeout=10, look_for_keys=False, allow_agent=False)

# Write via SFTP
sftp = ssh.open_sftp()
with sftp.open("C:/Users/29502/Desktop/anima_test.txt", "w") as f:
    f.write("Hello from ANIMA desktop node!\nCross-node file creation works!\n")
sftp.close()
print("File written via SFTP")

# Read back via cmd
stdin, stdout, stderr = ssh.exec_command(r'cmd /c "type C:\Users\29502\Desktop\anima_test.txt"')
print(f"Content: {stdout.read().decode().strip()}")
ssh.close()
