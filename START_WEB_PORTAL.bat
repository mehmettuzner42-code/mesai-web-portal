@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ==========================================
echo Mesai Web Portal Baslatiliyor
echo ==========================================
echo.

if not exist ".env.local" (
  echo [UYARI] .env.local bulunamadi.
  echo Ilk once WEB_PORTAL_ILK_KURULUM.bat calistir.
  pause
  exit /b 1
)

python --version >nul 2>nul
if errorlevel 1 (
  echo [HATA] Python bulunamadi. Python 3.11+ kurulu olmali.
  pause
  exit /b 2
)

echo Gereken paketler kontrol ediliyor...
pip install -r "requirements.txt"
if errorlevel 1 (
  echo [HATA] Paket kurulumu basarisiz.
  pause
  exit /b 3
)

echo.
echo Web portal aciliyor...
echo Tarayici adresi: http://127.0.0.1:5000
echo Durdurmak icin bu pencereye gelip CTRL+C bas.
echo.
python "app.py"

pause
exit /b 0
