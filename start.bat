@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Создаю окружение...
  py -3 -m venv .venv
)
.venv\Scripts\python.exe -c "import fastapi, uvicorn" 2>nul || (
  echo Ставлю зависимости...
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe run.py %*
pause
