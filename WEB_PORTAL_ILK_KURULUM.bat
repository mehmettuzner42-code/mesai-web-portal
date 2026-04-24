@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ==========================================
echo Mesai Web Portal - Ilk Kurulum
echo ==========================================
echo.

set /p SITE_BASE_URL=Site adresi (ornek: https://mesai.seninalanin.com):
set /p SECRET_KEY=Guclu SECRET_KEY (bos birakma):
set /p SMTP_HOST=SMTP host (ornek: smtp.gmail.com):
set /p SMTP_PORT=SMTP port (ornek: 587):
set /p SMTP_USERNAME=SMTP kullanici:
set /p SMTP_PASSWORD=SMTP sifre:
set /p SMTP_FROM=Mail gonderen adres:
set /p SMTP_USE_TLS=SMTP TLS (true/false, Enter=true):

if "%SMTP_USE_TLS%"=="" set "SMTP_USE_TLS=true"
if "%SMTP_PORT%"=="" set "SMTP_PORT=587"

if "%SITE_BASE_URL%"=="" (
  echo [HATA] Site adresi bos olamaz.
  pause
  exit /b 1
)
if "%SECRET_KEY%"=="" (
  echo [HATA] SECRET_KEY bos olamaz.
  pause
  exit /b 1
)

> ".env.local" (
  echo # Mesai web portal local config
  echo SITE_BASE_URL=%SITE_BASE_URL%
  echo SECRET_KEY=%SECRET_KEY%
  echo SMTP_HOST=%SMTP_HOST%
  echo SMTP_PORT=%SMTP_PORT%
  echo SMTP_USERNAME=%SMTP_USERNAME%
  echo SMTP_PASSWORD=%SMTP_PASSWORD%
  echo SMTP_FROM=%SMTP_FROM%
  echo SMTP_USE_TLS=%SMTP_USE_TLS%
  echo COOKIE_SECURE=true
  echo MAX_UPLOAD_MB=5
)

echo.
echo [BASARILI] .env.local olusturuldu.
echo Simdi START_WEB_PORTAL.bat ile baslatabilirsin.
pause
exit /b 0
