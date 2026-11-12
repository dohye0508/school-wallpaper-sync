"""Windows-only wallpaper and startup integration."""
import ctypes
import os
import subprocess
import sys

def ensure_autostart():
    """Register checked, idempotent automatic updates for the interactive user."""
    import winreg
    from adapters.scheduler import ensure_tasks
    if getattr(sys, "frozen", False):
        arguments = "--background"
    else:
        app_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
        arguments = subprocess.list2cmdline([app_path, "--background"])
    ensure_tasks(sys.executable, arguments)
    command = subprocess.list2cmdline([sys.executable]) + " " + arguments
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "SchoolWallpaperSync", 0, winreg.REG_SZ, command)


def get_screen_size():
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)



def set_wallpaper(path):
    # Toggling between wallpaper_1.png and wallpaper_2.png to force Windows to refresh the background
    dir_name = os.path.dirname(path)
    wp1 = os.path.abspath(os.path.join(dir_name, "wallpaper_1.png"))
    wp2 = os.path.abspath(os.path.join(dir_name, "wallpaper_2.png"))
    
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop")
        current_wp, _ = winreg.QueryValueEx(key, "Wallpaper")
        winreg.CloseKey(key)
        current_wp = os.path.abspath(current_wp)
    except Exception:
        current_wp = ""
        
    if current_wp.lower() == wp1.lower():
        target_path = wp2
    else:
        target_path = wp1
        
    import shutil
    try:
        shutil.copy2(path, target_path)
    except Exception as e:
        print(f"[오류] 바탕화면 파일 복사 실패: {e}")
        target_path = path
        
    SPI_SETDESKWALLPAPER = 20
    SPIF_UPDATEINIFILE = 0x01
    SPIF_SENDCHANGE = 0x02
    result = ctypes.windll.user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, target_path, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    )
    if not result:
        raise OSError("Windows 배경화면 적용에 실패했습니다.")


