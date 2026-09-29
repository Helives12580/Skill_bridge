@echo off
cd /d "%~dp0"

rem ---- Locate Python. If none of these match, put your own interpreter path here. ----
set "BRIDGE_PY="
for %%P in ("%LOCALAPPDATA%\Programs\ComfyUI\resources\python_embeded\python.exe" "%USERPROFILE%\ComfyUI\python_embeded\python.exe" "D:\Comfy-Desktop\ComfyUI\standalone-env\python.exe" "python.exe") do if not defined BRIDGE_PY if exist %%~P set "BRIDGE_PY=%%~P"
if not defined BRIDGE_PY (
  echo   [bridge] python not found. Edit the for-loop above with your own path.
  pause
  exit /b 1
)

echo ============================================================
echo   DSH skill-bridge
echo ============================================================

netstat -ano | findstr ":8899" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 goto running

echo   [bridge] starting ...
start /b "skill-bridge" "%BRIDGE_PY%" -X utf8 "%~dp0dsh_skill_bridge.py"
rem ping instead of timeout: timeout fails when stdin is redirected
ping -n 3 127.0.0.1 >nul 2>&1
echo   [bridge] ready   http://127.0.0.1:8899/v1
echo   [bridge] log     bridge.log
goto done

:running
echo   [bridge] already running, reusing it

:done
echo.
echo   Keep this window open while ComfyUI needs the bridge.
echo   Press any key to stop the bridge and close.
pause >nul
