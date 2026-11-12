"""Desktop entry point with durable diagnostics for windowless executions."""
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from datetime import datetime

import main
from core.storage import atomic_json


def run():
    handler = RotatingFileHandler(os.path.join(main.BASE_DIR, 'sync.log'),
                                  maxBytes=512_000, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format='%(asctime)s %(levelname)s %(message)s')
    if '--background' not in sys.argv:
        import settings_gui
        app = settings_gui.App()
        app.mainloop()
        return 0
    result = {'started_at': datetime.now().astimezone().isoformat(), 'pid': os.getpid()}
    logging.info('Background update started pid=%s', os.getpid())
    try:
        cfg = main.load_config()
        if not cfg or not cfg.get('school_code'):
            raise RuntimeError('학교 설정이 없습니다. 설정 창에서 학교를 선택하세요.')
        applied = main.update_once(cfg)
        result['status'] = 'success' if applied else 'discarded'
        result['cache_states'] = main.load_data_cache(cfg).get('states', {})
        logging.info('Background update %s: %s', result['status'], result['cache_states'])
        return 0
    except Exception as error:
        result.update(status='error', error=str(error))
        logging.exception('Background update failed')
        return 1
    finally:
        result['finished_at'] = datetime.now().astimezone().isoformat()
        atomic_json(os.path.join(main.BASE_DIR, 'last_run.json'), result)


if __name__ == '__main__':
    sys.exit(run())
