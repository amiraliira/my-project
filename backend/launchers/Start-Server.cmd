@echo off
setlocal
echo Use the installed Start-Pak.cmd normally. This shortcut asks for its installation root.
"%~dp0runtime\python.exe" -B "%~dp0tools\release_manager.py" run
if errorlevel 1 pause
endlocal
