import logging
import os

from .bot import LCBoutiqueBot
from .config import Config


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    LCBoutiqueBot(Config.from_env()).run()


if __name__ == "__main__":
    main()
