@echo off

title Tomo

cd /d "%~dp0"

echo.
echo ================================
echo      TOMO
echo ================================
echo.

if not exist ".venv\Scripts\python.exe" (

    echo Creando entorno virtual...

    py -m venv .venv

)

echo.
echo Instalando dependencias...
echo.

call .venv\Scripts\activate

py -m pip install -r requirements.txt

echo.
echo Iniciando aplicacion...
echo.

py app.py

pause