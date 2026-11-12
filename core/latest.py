"""Single worker with coalescing: only the latest pending preview is rendered."""
import threading
from copy import deepcopy

class LatestWorker:
    def __init__(self, work):
        self.work = work
        self.lock = threading.RLock()
        self.version = 0
        self.pending = None
        self.running = False

    def invalidate(self):
        with self.lock:
            self.version += 1
            self.pending = None

    def submit(self, value):
        with self.lock:
            self.version += 1
            self.pending = (self.version, deepcopy(value))
            if not self.running:
                self.running = True
                threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            with self.lock:
                if self.pending is None:
                    self.running = False
                    return
                version, value = self.pending
                self.pending = None
            def commit(action):
                with self.lock:
                    if version == self.version:
                        action()
                        return True
                return False
            try:
                self.work(value, commit)
            except Exception as error:
                print(f'[Preview failed] {error}')
