@echo off
REM Monday job-digest habit: fresh scan, then top-10 digest of the week.
REM Run manually any time, or register with Task Scheduler (see instructions.txt).
cd /d "%~dp0"
call .venv\Scripts\activate.bat

echo ============================================================
echo  LocalJobScout — Weekly Digest  (%DATE%)
echo ============================================================
echo.
echo [1/2] Scanning job boards for fresh postings...
python -m localjobscout --once

echo.
echo [2/2] Your top jobs this week:
REM --digest-send also emails it IF alerts SMTP is configured in config.yaml;
REM either way the table prints here so you can act on it now.
python -m localjobscout --digest --digest-send --digest-days 7 --digest-top 10

echo.
echo ------------------------------------------------------------
echo  Now open the queue and apply to your top 5:
echo    python -m localjobscout --manual-queue --open 5
echo ------------------------------------------------------------
pause
