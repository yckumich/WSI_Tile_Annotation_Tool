@echo off
setlocal EnableDelayedExpansion

:: ===========================================
:: WSI Tile Annotation Tool - Setup & Launch (Windows)
:: ===========================================
::
:: This script does everything needed to run the tool on a fresh machine:
::   1. Finds a compatible Python (3.10 or 3.11)
::   2. Checks for the OpenSlide system library
::   3. Creates a virtual environment (if needed)
::   4. Installs required packages (if needed)
::   5. Registers the Jupyter kernel
::   6. Cleans up stale files
::   7. Clears notebook outputs
::   8. Launches the notebook in your browser
::
:: Usage:
::   cd \path\to\WSI_Tile_Annotation_Tool
::   setup.bat
::
:: ===========================================

echo.
echo ===========================================
echo   WSI Tile Annotation Tool - Setup ^& Launch
echo ===========================================
echo.

rem ───────────────────────────────────────────
rem Locate this script's directory
rem ───────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "VENV_DIR=%SCRIPT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "KERNEL_NAME=wsi_tile_annotation"
set "NOTEBOOK=viewer.ipynb"

cd /d "%SCRIPT_DIR%"

rem ───────────────────────────────────────────
rem Step 1: Find Python 3.10 or 3.11
rem ───────────────────────────────────────────
echo [Step 1] Looking for Python 3.10 or 3.11...
echo.

set "PYTHON_CMD="

where py >nul 2>&1
if not errorlevel 1 (
    for %%V in (3.11 3.10) do (
        if not defined PYTHON_CMD (
            py -%%V -c "import sys" >nul 2>&1
            if not errorlevel 1 (
                set "PYTHON_CMD=py -%%V"
                echo   Found: py -%%V
            )
        )
    )
)

if not defined PYTHON_CMD call :TryBareCommand python3
if not defined PYTHON_CMD call :TryBareCommand python

if not defined PYTHON_CMD (
    echo    No compatible Python found.
    echo.
    echo   This tool requires Python 3.10 or 3.11.
    echo.
    echo   To install from python.org ^(includes the py launcher^):
    echo     https://www.python.org/downloads/
    echo     ^(check "Add python.exe to PATH" during install^)
    echo.
    echo   To install with winget:
    echo     winget install Python.Python.3.11
    echo.
    echo   After installing, run this script again.
    exit /b 1
)

echo.

rem ───────────────────────────────────────────
rem Step 2: Check for the OpenSlide system library
rem ───────────────────────────────────────────
rem openslide-python (in requirements.txt) is just a wrapper -- it loads the
rem real OpenSlide DLL at import time. Unlike macOS/Homebrew, there's no
rem universal Windows package manager to check here, so this step just
rem prints what's needed; whether it actually loads is verified for real in
rem Step 4, with more specific guidance if that import fails.
echo [Step 2] OpenSlide system library...
echo.
echo   This tool requires the OpenSlide Windows binaries installed
echo   separately from the openslide-python package installed later by
echo   this script.
echo.
echo   To install:
echo     1. Download the latest Windows build from:
echo        https://openslide.org/download/
echo     2. Extract it somewhere permanent ^(e.g. C:\openslide^)
echo     3. Add its "bin" folder to your PATH ^(e.g. C:\openslide\bin^)
echo     4. Open a NEW terminal so the updated PATH takes effect
echo.
echo   ^(This will be verified automatically in Step 4 below.^)
echo.

rem ───────────────────────────────────────────
rem Step 3: Create virtual environment
rem ───────────────────────────────────────────
if exist "%VENV_DIR%" (
    echo [Step 3] Virtual environment already exists.

    if not exist "%VENV_PY%" (
        echo     Broken venv detected ^(moved folder?^). Recreating...
        rmdir /s /q "%VENV_DIR%"
    )
)

if not exist "%VENV_DIR%" (
    echo [Step 3] Creating virtual environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"

    if errorlevel 1 (
        echo    Failed to create virtual environment.
        echo   Make sure Python 3.10 or 3.11 is properly installed.
        exit /b 1
    )
    echo   Created at: %VENV_DIR%
)

echo.

rem ───────────────────────────────────────────
rem Step 4: Install packages
rem ───────────────────────────────────────────
echo [Step 4] Checking packages...

"%VENV_PY%" -c "import openslide; import ipywidgets; import ipyevents; import ipyfilechooser; import PIL" >nul 2>&1
if errorlevel 1 (
    echo   Installing required packages from requirements.txt...
    echo   ^(This may take a few minutes on first run^)
    echo.

    "%VENV_DIR%\Scripts\pip.exe" install --upgrade pip --quiet
    "%VENV_DIR%\Scripts\pip.exe" install -r "%SCRIPT_DIR%\requirements.txt" --quiet

    if errorlevel 1 (
        echo.
        echo    Package installation failed.
        echo   Check your internet connection and try again.
        exit /b 1
    )

    "%VENV_PY%" -c "import openslide" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo    openslide-python installed, but the OpenSlide DLL could not
        echo   be loaded -- the system library from Step 2 isn't on PATH.
        echo.
        echo   Double check:
        echo     1. You downloaded the OpenSlide Windows binaries from
        echo        https://openslide.org/download/
        echo     2. Its "bin" folder is on your PATH
        echo     3. You opened a NEW terminal after updating PATH
        echo.
        exit /b 1
    )

    echo   All packages installed.
) else (
    echo   All packages already installed.
)

echo.

rem ───────────────────────────────────────────
rem Step 5: Register Jupyter kernel
rem ───────────────────────────────────────────
echo [Step 5] Registering Jupyter kernel...

"%VENV_PY%" -m ipykernel install --user --name "%KERNEL_NAME%" --display-name "WSI Tile Annotation Tool (Python 3)" >nul 2>&1

if not errorlevel 1 (
    echo   Kernel registered: %KERNEL_NAME%
    echo     Python: %VENV_PY%
) else (
    echo    Kernel registration failed.
    echo     You may need to select the kernel manually in Jupyter.
)

echo.

rem ───────────────────────────────────────────
rem Step 6: Clean up stale files
rem ───────────────────────────────────────────
echo [Step 6] Cleaning up...

for /d /r "%SCRIPT_DIR%\utils" %%D in (__pycache__) do (
    if exist "%%D" rmdir /s /q "%%D"
)
for /d /r "%SCRIPT_DIR%\widgets" %%D in (__pycache__) do (
    if exist "%%D" rmdir /s /q "%%D"
)
if exist "%SCRIPT_DIR%\.ipynb_checkpoints" rmdir /s /q "%SCRIPT_DIR%\.ipynb_checkpoints"

echo   Stale files removed.
echo.

rem ───────────────────────────────────────────
rem Step 7: Clear notebook outputs
rem ───────────────────────────────────────────
echo [Step 7] Clearing notebook outputs...

"%VENV_DIR%\Scripts\jupyter.exe" nbconvert ^
    --ClearOutputPreprocessor.enabled=True ^
    --to notebook ^
    --inplace ^
    "%SCRIPT_DIR%\%NOTEBOOK%" >nul 2>&1

if not errorlevel 1 (
    echo    Notebook outputs cleared.
) else (
    echo   Could not clear notebook outputs ^(non-critical^).
)

echo.

rem ───────────────────────────────────────────
rem Step 8: Launch notebook
rem ───────────────────────────────────────────
echo [Step 8] Launching notebook...
echo.
echo ===========================================
echo   The notebook will open in your browser.
echo   To stop the server, press Ctrl+C here.
echo ===========================================
echo.

"%VENV_DIR%\Scripts\jupyter.exe" notebook "%SCRIPT_DIR%\%NOTEBOOK%"

endlocal
exit /b 0

:TryBareCommand
where %~1 >nul 2>&1
if errorlevel 1 goto :eof
for /f "tokens=2 delims= " %%P in ('%~1 --version 2^>^&1') do set "PY_VER=%%P"
echo !PY_VER! | findstr /r "^3\.10\." >nul
if not errorlevel 1 (
    set "PYTHON_CMD=%~1"
    echo   Found: %~1 ^(!PY_VER!^)
    goto :eof
)
echo !PY_VER! | findstr /r "^3\.11\." >nul
if not errorlevel 1 (
    set "PYTHON_CMD=%~1"
    echo   Found: %~1 ^(!PY_VER!^)
    goto :eof
)
goto :eof
