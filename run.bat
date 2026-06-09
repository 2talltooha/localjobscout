@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo LocalJobScout starting — press Ctrl+C to stop
python -m localjobscout
