@echo off
REM Build VideoLib standalone exe
python -m PyInstaller --noconfirm --clean --onefile --windowed --name VideoLib --collect-all av main.py
echo Done: dist\VideoLib.exe
