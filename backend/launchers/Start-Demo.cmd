@echo off
setlocal
for %%I in ("%~dp0.") do set "PAK_DEMO_ROOT=%%~fI"
"%~dp0runtime\python.exe" -B -m pak_maintenance.demo --root "%PAK_DEMO_ROOT%"
if errorlevel 1 pause
endlocal
