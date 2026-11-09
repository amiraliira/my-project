@echo off
setlocal
"%~dp0runtime\python.exe" -B "%~dp0tools\release_manager.py" rollback
pause
endlocal
