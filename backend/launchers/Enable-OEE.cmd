@echo off
setlocal
chcp 65001 >nul
for %%I in ("%~dp0.") do set "PAK_OEE_ROOT=%%~fI"
if not exist "%PAK_OEE_ROOT%\runtime\python.exe" goto incomplete
echo ابتدا پنجره اجرای برنامه را ببندید. این ابزار پشتیبان می‌گیرد و بخش کارکرد خطوط را اضافه می‌کند.
"%PAK_OEE_ROOT%\runtime\python.exe" -B "%PAK_OEE_ROOT%\tools\enable_oee.py" --local-root "%PAK_OEE_ROOT%"
if errorlevel 1 (
 echo فعال‌سازی انجام نشد؛ پیام بالا را بررسی کنید. اطلاعات قبلی بازنشانی نشده‌اند.
 pause
 exit /b 1
)
echo بخش کارکرد خطوط آماده شد. برنامه را با میان‌بر معمول اجرا کنید.
pause
endlocal
exit /b 0
:incomplete
echo این فایل باید داخل پوشه کامل نرم‌افزار اجرا شود.
pause
endlocal
exit /b 1
