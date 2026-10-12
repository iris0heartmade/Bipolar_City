import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

from backend.config import ROOT


def setup_logging():
    directory = ROOT / "logs"
    directory.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    file = RotatingFileHandler(directory / ("bipole_%s.log" % datetime.now().strftime("%Y%m%d_%H%M%S")),
                               maxBytes=10*1024*1024,backupCount=7,encoding="utf-8")
    file.setLevel(logging.DEBUG)
    file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"))
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root.addHandler(file)
    root.addHandler(console)
