import argparse

import uvicorn

from scripticus_server import __version__
from scripticus_server.app import app


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripticus-svr",
        description=(
            "Scripticus index service — search, resolution, and publishing "
            "for shared scripts."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind to (default: %(default)s).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to serve on (default: %(default)s).",
    )
    return parser.parse_args(argv)


def _banner(host: str, port: int) -> str:
    return (
        f"scripticus-svr {__version__} — serving on http://{host}:{port} "
        f"(interactive API docs at http://{host}:{port}/docs)"
    )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    print(_banner(args.host, args.port), flush=True)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
