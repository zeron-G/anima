@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$sc = $ws.CreateShortcut([IO.Path]::Combine($ws.SpecialFolders('Desktop'), 'ANIMA.lnk')); " ^
  "$sc.TargetPath = '%~dp0ANIMA.bat'; " ^
  "$sc.WorkingDirectory = '%~dp0'; " ^
  "$sc.Description = 'ANIMA Desktop'; " ^
  "$sc.WindowStyle = 7; " ^
  "$sc.Save(); " ^
  "Write-Host 'Shortcut created on Desktop: ANIMA.lnk'"
pause
