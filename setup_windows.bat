@echo off
REM One-time setup for WeatherFit AI on Windows. Double-click or run from Command Prompt in this folder.
cd /d "%~dp0"
where py >nul 2>nul || (echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause & exit /b 1)
if not exist .venv ( py -m venv .venv || (echo Could not create the virtual environment. & pause & exit /b 1) )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo Install failed - see the message above. & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
echo.
echo Setup finished. Optionally open .env and add GEMINI_API_KEY / WEATHERAPI_KEY, then double-click run_windows.bat
pause
