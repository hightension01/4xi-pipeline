@echo off
title 4XI Pipeline - List Only
echo ============================================================
echo   4XI Studios - List Only
echo   Processes drafts already on nifty.ai
echo ============================================================
echo.
:ask_count
set /p COUNT="How many drafts to list? "
if "%COUNT%"=="" goto ask_count
echo %COUNT%| findstr /r "^[0-9][0-9]*$" >nul || goto ask_count
echo.

set REPO=%USERPROFILE%\4xi-pipeline
cd /d "%REPO%\nifty-uploader"

echo Listing drafts on nifty.ai...
echo ─────────────────────────────────────────────────────────────
node list.auto.js --count %COUNT%
if %ERRORLEVEL% NEQ 0 (echo. & echo ERROR: Listing failed. & pause & exit /b 1)

echo.
echo ============================================================
echo   Done!
echo ============================================================
echo.
cmd /k echo Window staying open. Type EXIT to close.
