@echo off
:: UE Asset Toolkit - Index Management wrapper
:: Usage:
::   .\index.bat              Show index status
::   .\index.bat --all        Full hybrid index
::   .\index.bat --quick      Quick index
::   .\index.bat --source     Index C++ source
::   .\index.bat --status     Detailed statistics

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python not found
    exit /b 1
)

python "%~dp0index.py" %*
