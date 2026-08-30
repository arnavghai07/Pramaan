@echo off
REM PRAMAAN frontend startup script.
REM Works regardless of the directory it is launched from: %~dp0 always
REM resolves to the folder this .bat file lives in (the repo root), and the
REM frontend lives in the "web" subfolder of it.

cd /d "%~dp0web"

echo Frontend directory: %cd%
echo Starting PRAMAAN frontend (npm run dev) on http://localhost:3000 ...
npm run dev

echo.
echo ---------------------------------------------------------------
echo npm run dev has stopped. Review any error output above.
echo ---------------------------------------------------------------
pause
