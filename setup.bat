@echo off
:: UE Asset Toolkit Setup - Windows wrapper
:: This script just calls setup.py with Python
::
:: Usage:
::   .\setup.bat                              Build only
::   .\setup.bat C:\Path\To\Project.uproject  Build + configure project
::   .\setup.bat C:\Path\To\Project.uproject --index  Build + configure + index
::   .\setup.bat --help                       Show help

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python not found. Install Python 3.10+ from https://python.org
    exit /b 1
)

python "%~dp0setup.py" %*
