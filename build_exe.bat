@echo off
rem Builds F5Overlay.exe from f5_overlay.py (run this file on Windows, in the folder that has f5_overlay.py)
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install it from python.org and tick "Add Python to PATH". & pause & exit /b 1)
python -m pip install --upgrade pip pyinstaller || (echo Could not install PyInstaller. & pause & exit /b 1)
python -m PyInstaller --noconfirm --clean --onefile --console --uac-admin --name F5Overlay f5_overlay.py || (echo Build failed. & pause & exit /b 1)
echo.
echo Done: dist\F5Overlay.exe
echo Copy it anywhere. f5_overlay.ini (and an optional Config.txt) are created / read next to the exe.
pause
