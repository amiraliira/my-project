@echo off
setlocal
"%~dp0runtime\python.exe" -B "%~dp0tools\verify_package.py" --root "%~dp0."
pause
endlocal
