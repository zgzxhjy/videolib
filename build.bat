@echo off
REM Build VideoLib standalone exe
python -m PyInstaller --noconfirm --clean --onefile --windowed --name VideoLib --icon app.ico --add-data "app.ico;." --add-data "vendor/mpv/mpv.exe;vendor/mpv" --collect-all av --additional-hooks-dir build-hooks main.py
echo Done: dist\VideoLib.exe
