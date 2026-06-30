@echo off
title 4XI Pipeline - Upload + List
echo ============================================================
echo   4XI Studios - Upload + List
echo   Skips watcher, uploads unprocessed items from itemsDir
echo ============================================================
echo.
:ask_count
set /p COUNT="How many items to upload? "
if "%COUNT%"=="" goto ask_count
echo %COUNT%| findstr /r "^[0-9][0-9]*$" >nul || goto ask_count
echo.

set REPO=%USERPROFILE%\4xi-pipeline
cd /d "%REPO%\nifty-uploader"

echo [1/2] Uploading processed images to nifty.ai...
echo ─────────────────────────────────────────────────────────────
node upload.auto.js --count %COUNT%
if %ERRORLEVEL% NEQ 0 (echo. & echo ERROR: Upload failed. & pause & exit /b 1)

echo.
echo [2/2] Listing drafts on nifty.ai...
echo ─────────────────────────────────────────────────────────────
node list.auto.js --count %COUNT%
if %ERRORLEVEL% NEQ 0 (echo. & echo ERROR: Listing failed. & pause & exit /b 1)

echo.
echo ============================================================
echo   Done!
echo ============================================================
echo.
cmd /k echo Window staying open. Type EXIT to close.
