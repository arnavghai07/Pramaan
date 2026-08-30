@echo off
REM PRAMAAN backend startup script.
REM Works regardless of the directory it is launched from: %~dp0 always
REM resolves to the folder this .bat file lives in (the repo root).

cd /d "%~dp0"

echo Repository root: %cd%
echo Activating .venv ...
call ".venv\Scripts\activate.bat"

echo Starting PRAMAAN backend (uvicorn) on http://localhost:8000 ...
uvicorn api.main:app --reload --port 8000

echo.
echo ---------------------------------------------------------------
echo uvicorn has stopped. Review any error output above.
echo ---------------------------------------------------------------
pause
