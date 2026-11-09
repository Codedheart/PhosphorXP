@echo off
rem ============================================================================
rem  Builds a share-ready copy of Phosphor XP that needs NO Python:
rem     PhosphorXP-windows.zip   ->  friends unzip it and run PhosphorXP.exe
rem  Only the person building needs Python (3.10+). Run this on Windows.
rem ============================================================================
cd /d "%~dp0"
setlocal

set "PY="
for %%V in (3.13 3.12 3.11 3.10) do if not defined PY (py -%%V -c "1" >nul 2>&1 && set "PY=py -%%V")
if not defined PY (python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1 && set "PY=python")
if not defined PY (echo Building needs Python 3.10+ from python.org on THIS PC. & pause & exit /b 1)

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1 || rmdir /s /q .venv
)
if not exist .venv\Scripts\python.exe (
  echo Setting up build environment with %PY% ...
  %PY% -m venv .venv || goto :err
)
echo Installing build tools...
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt "pyinstaller>=6.3" || goto :err

if not exist phosphor\assets\VT323-Regular.ttf (
  echo Fetching VT323 font...
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing 'https://github.com/google/fonts/raw/main/ofl/vt323/VT323-Regular.ttf' -OutFile 'phosphor\assets\VT323-Regular.ttf'; Invoke-WebRequest -UseBasicParsing 'https://github.com/google/fonts/raw/main/ofl/vt323/OFL.txt' -OutFile 'phosphor\assets\VT323-OFL.txt' } catch { Write-Host '  font skipped (falls back to Lucida Console)' }"
)

rem Stamp the version from the git tag on this commit (untagged builds report 'dev'
rem and never offer to update themselves)
set "TAG="
for /f "delims=" %%T in ('git describe --tags --exact-match 2^>nul') do set "TAG=%%T"
if defined TAG (
  > phosphor\_build_version.py echo VERSION = "%TAG:v=%"
  echo Version %TAG%
) else (
  if exist phosphor\_build_version.py del phosphor\_build_version.py
  echo No tag on this commit: building as 'dev' ^(no auto-update^)
)

echo Building PhosphorXP.exe ...
if exist build rmdir /s /q build
if exist dist\PhosphorXP rmdir /s /q dist\PhosphorXP
.venv\Scripts\python -m PyInstaller --noconfirm --clean --log-level WARN ^
  --onedir --noconsole --name PhosphorXP --icon phosphor\assets\icon.ico ^
  --add-data "phosphor\assets;phosphor\assets" ^
  --hidden-import PIL.ImageTk ^
  --exclude-module matplotlib --exclude-module scipy --exclude-module pandas ^
  --exclude-module pytest --exclude-module IPython ^
  phosphor_xp.py || goto :err

copy /y "share\READ ME FIRST.txt" "dist\PhosphorXP\" >nul
copy /y THIRD_PARTY.md "dist\PhosphorXP\" >nul
if exist "dist\PhosphorXP\third_party" rmdir /s /q "dist\PhosphorXP\third_party"
xcopy /e /i /q third_party "dist\PhosphorXP\third_party" >nul

echo Zipping...
if exist PhosphorXP-windows.zip del PhosphorXP-windows.zip
powershell -NoProfile -Command "Compress-Archive -Path 'dist\PhosphorXP' -DestinationPath 'PhosphorXP-windows.zip' -CompressionLevel Optimal" || goto :err

echo.
echo  Done!  Send your friends:  %CD%\PhosphorXP-windows.zip
echo  (They unzip it and double-click PhosphorXP\PhosphorXP.exe - no Python needed.)
echo.
explorer /select,"%CD%\PhosphorXP-windows.zip"
pause
exit /b 0

:err
echo.
echo Build failed - see the messages above.
pause
exit /b 1
