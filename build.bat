@echo off
REM Build VideoLib standalone exe
python -m PyInstaller --noconfirm --clean --onefile --windowed --name VideoLib --icon app.ico --collect-all av main.py
echo Done: dist\VideoLib.exe
