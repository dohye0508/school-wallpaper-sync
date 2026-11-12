"""Preserve additional SSL policy data when using a managed Python runtime.

Stock Python needs no extra data. Some managed distributions load a local trust
policy file from ssl.py's directory; freezing must retain that policy unchanged.
"""
from pathlib import Path
from PyInstaller.utils.hooks import get_module_file_attribute

policy = Path(get_module_file_attribute('ssl')).with_name('codex-rejected-cas.pem')
datas = [(str(policy), '.')] if policy.is_file() else []
