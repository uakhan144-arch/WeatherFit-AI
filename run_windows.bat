@echo off
REM Starts WeatherFit AI. Run setup_windows.bat once first.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat ( echo Run setup_windows.bat first. & pause & exit /b 1 )
call .venv\Scripts\activate.bat
python -m streamlit run app.py
pause
