@echo off
rem Build the Windows app: dist\PokeUDS\PokeUDS.exe and dist\PokeUDS-windows.zip (docs\release.md).
rem Double-click it, or run it from a command prompt; extra options go to scripts\build_exe.py
rem (for example: build_exe.cmd --no-zip, build_exe.cmd --no-helper, build_exe.cmd --icon other.ico).
rem
rem Needs Python 3.11 or newer (3.13 recommended) for the first run, which makes the .venv folder,
rem and the .NET 10 SDK to bundle the PKHeX helper; without it the app is built without the helper.
rem dist\PokeUDS\Bank, a bank made by running the built exe, is kept across rebuilds.

setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo Making the Python environment in .venv ...
    py -3.13 -m venv .venv >nul 2>nul || py -3 -m venv .venv >nul 2>nul || python -m venv .venv
    if not exist "%PY%" (
        echo.
        echo Could not make .venv: install Python 3.13 from python.org, then run this again.
        goto :failed
    )
)

echo Checking the build tools ...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements-gui.txt pyinstaller
if errorlevel 1 goto :failed

set "HELPER="
where dotnet >nul 2>nul
if errorlevel 1 (
    echo The .NET SDK was not found: building without the bundled PKHeX helper.
    set "HELPER=--no-helper"
)

"%PY%" scripts\build_exe.py %HELPER% %*
if errorlevel 1 goto :failed

echo.
echo Built dist\PokeUDS\PokeUDS.exe and dist\PokeUDS-windows.zip
set "CODE=0"
goto :end

:failed
echo.
echo The build failed: see the messages above.
set "CODE=1"

:end
rem Keep the window open when the file was double-clicked, so the result can be read.
echo %cmdcmdline% | "%SystemRoot%\System32\find.exe" /i "%~0" >nul && pause
endlocal & exit /b %CODE%
