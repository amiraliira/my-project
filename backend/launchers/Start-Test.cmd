@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0.") do set "PAK_TEST_ROOT=%%~fI"
if not exist "%PAK_TEST_ROOT%\runtime\python.exe" goto incomplete
if not exist "%PAK_TEST_ROOT%\runtime\python314.dll" goto incomplete
if not exist "%PAK_TEST_ROOT%\frontend\index.html" goto incomplete
if not exist "%PAK_TEST_ROOT%\test-payload\acceptance-data.json" goto incomplete
if not exist "%PAK_TEST_ROOT%\tools\verify_package.py" goto incomplete
"%PAK_TEST_ROOT%\runtime\python.exe" -B "%PAK_TEST_ROOT%\tools\verify_package.py" --root "%PAK_TEST_ROOT%"
if errorlevel 1 (
  echo فایل‌های برنامه ناقص یا تغییرکرده‌اند. فایل ZIP را دوباره در یک پوشه تازه استخراج کنید.
  pause
  exit /b 1
)
"%PAK_TEST_ROOT%\runtime\python.exe" -B -u -m pak_maintenance.local_test --root "%PAK_TEST_ROOT%"
if errorlevel 1 (
  echo.
  echo برنامه اجرا نشد. پیام بالا و فایل test-data\startup-error.json را بررسی کنید.
  pause
)
endlocal
exit /b
:incomplete
echo فایل‌های برنامه کامل نیستند. فایل ZIP را کامل استخراج کنید و سپس شروع تست را اجرا کنید.
echo The complete ZIP must be extracted before starting this application.
pause
endlocal
exit /b 1
