"""Keep ``python -m app.worker`` as a stable command."""

from app.bootstrap.runtime import open_bridge
from app.infrastructure.settings import Settings
from app.presentation.worker import run


def main():
    run(Settings(), open_bridge)


if __name__ == "__main__":
    main()
