@echo off
title 4XI Pipeline
echo ============================================================
echo   4XI Studios - Full Pipeline
echo   Watcher ^> Upload ^> List
echo ============================================================
echo.
echo Make sure ComfyUI is running before continuing!
echo.
:ask_count
set /p COUNT="How many items to process? "
if "%COUNT%"=="" goto ask_count
echo %COUNT%| findstr /r "^[0-9][0-9]*$" >nul || goto ask_count
echo.

set REPO=%USERPROFILE%\4xi-pipeline

:: ── Step 1: Watcher ───────────────────────────────────────────────────────────
echo [1/3] Processing photos through ComfyUI...
echo ─────────────────────────────────────────────────────────────
"%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe" "%REPO%\watcher.py" --single-pass --count %COUNT%
if %ERRORLEVEL% NEQ 0 (echo. & echo ERROR: Watcher failed. & pause & exit /b 1)

:: ── Step 2: Upload ────────────────────────────────────────────────────────────
echo.
echo [2/3] Uploading processed images to nifty.ai...
echo ─────────────────────────────────────────────────────────────
cd /d "%REPO%\nifty-uploader"
node upload.auto.js
if %ERRORLEVEL% NEQ 0 (echo. & echo ERROR: Upload failed. & pause & exit /b 1)

:: ── Wait ──────────────────────────────────────────────────────────────────────
echo.
echo Waiting 2 minutes for nifty.ai to generate listings...
timeout /t 120 /nobreak

:: ── Step 3: List ──────────────────────────────────────────────────────────────
echo.
echo [3/3] Listing drafts on nifty.ai...
echo ─────────────────────────────────────────────────────────────
node list.auto.js
if %ERRORLEVEL% NEQ 0 (echo. & echo ERROR: Listing failed. & pause & exit /b 1)

echo.
echo ============================================================
echo   Pipeline complete! %COUNT% item(s) processed.
echo ============================================================
echo.
cmd /k echo Window staying open. Type EXIT to close.
