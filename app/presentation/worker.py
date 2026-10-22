import logging
import time

log = logging.getLogger("bridge-worker")


def run(cfg, bridge_factory):
    logging.basicConfig(level=logging.INFO)
    with bridge_factory(cfg) as bridge:
        try:
            while True:
                try:
                    log.info("Cycle: %s", bridge.cycle())
                except Exception:
                    log.exception("Cycle failed")
                time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            pass
