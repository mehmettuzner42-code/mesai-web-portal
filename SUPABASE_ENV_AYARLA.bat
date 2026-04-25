@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ==========================================
echo Mesai Web Portal - Supabase Ayarlari
echo ==========================================
echo.

set /p SUPABASE_URL=Supabase URL (ornek: https://xxxx.supabase.co): 
set /p SUPABASE_ANON_KEY=Supabase anon key (sb_publishable_...): 

if "%SUPABASE_URL%"=="" (
  echo [HATA] URL bos olamaz.
  pause
  exit /b 1
)
if "%SUPABASE_ANON_KEY%"=="" (
  echo [HATA] anon key bos olamaz.
  pause
  exit /b 1
)

if not exist ".env.local" (
  > ".env.local" (
    echo SECRET_KEY=degistir-beni
    echo SITE_BASE_URL=http://127.0.0.1:5000
  )
)

powershell -NoProfile -Command ^
  "$p='.env.local';$url='%SUPABASE_URL%';$key='%SUPABASE_ANON_KEY%';" ^
  "$t=Get-Content -Path $p -Raw;" ^
  "if($t -notmatch '(?m)^SUPABASE_URL='){ $t += [Environment]::NewLine + 'SUPABASE_URL=' + $url } else { $t=[regex]::Replace($t,'(?m)^SUPABASE_URL=.*$','SUPABASE_URL=' + $url) };" ^
  "if($t -notmatch '(?m)^SUPABASE_ANON_KEY='){ $t += [Environment]::NewLine + 'SUPABASE_ANON_KEY=' + $key } else { $t=[regex]::Replace($t,'(?m)^SUPABASE_ANON_KEY=.*$','SUPABASE_ANON_KEY=' + $key) };" ^
  "$enc=New-Object System.Text.UTF8Encoding($false);[System.IO.File]::WriteAllText($p,$t,$enc)"

echo.
echo [BASARILI] Supabase ayarlari .env.local dosyasina yazildi.
echo Simdi START_WEB_PORTAL.bat calistir.
pause
exit /b 0
