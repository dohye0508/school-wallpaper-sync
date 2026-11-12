"""Crash-safe persistence and a shared lock for desktop writers."""
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager

_lock = threading.RLock()

def atomic_json(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

@contextmanager
def desktop_transaction(directory):
    # Serialize GUI threads AND scheduled/background processes. OS releases on exit.
    with _lock:
        with open(os.path.join(directory, '.sync.lock'), 'a+b') as stream:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0, 2)
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                deadline = time.monotonic() + 60
                while True:
                    stream.seek(0)
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Wallpaper update is busy')
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)
