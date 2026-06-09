@echo off
rem LocalJobScout scheduled scan — invoked hourly by Windows Task Scheduler
rem (task name: "LocalJobScout Scan"). Uses the venv python; the LLM
rem suitability/qualification gate runs through the Claude CLI subscription
rem backend so no ANTHROPIC_API_KEY is needed.
cd /d "%~dp0.."
set LOCALJOBSCOUT_USE_CLI=1
echo. >> "data\scan_task.log"
echo ===== scan %date% %time% ===== >> "data\scan_task.log"
".venv\Scripts\python.exe" -m localjobscout --once >> "data\scan_task.log" 2>&1
