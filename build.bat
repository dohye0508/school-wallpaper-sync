@echo off
cd /d "%~dp0"

echo Building SchoolWallpaperSettings.exe...
python -m PyInstaller --onefile --windowed --name "SchoolWallpaperSettings" --icon="icon.ico" --add-data "fonts;fonts" --add-data "KOPUBWORLD_OTF_FONTS2026;KOPUBWORLD_OTF_FONTS2026" --noconfirm app.py

echo Moving executable...
move /y "dist\SchoolWallpaperSettings.exe" "SchoolWallpaperSettings.exe" >nul

echo Cleaning up...
rmdir /s /q build
rmdir /s /q build_gui
rmdir /s /q dist
del /q *.spec
del /q "?숆탳諛뷀깢?붾㈃?ㅼ젙.exe"
rmdir /s /q __pycache__ 2>nul

echo Build complete!
