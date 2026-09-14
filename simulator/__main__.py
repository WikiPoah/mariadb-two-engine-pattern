"""Run with python -m simulator."""

from simulator.config import database_config, parse_args
from simulator.runner import run


def main() -> int:
    options = parse_args()
    try:
        config = database_config()
    except ValueError as error:
        print(f'Configuration error: {error}', flush=True)
        return 1
    return run(options, config)


if __name__ == '__main__':
    raise SystemExit(main())
