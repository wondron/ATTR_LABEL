# -*- coding: utf-8 -*-
"""启动本地多属性标注 Web 界面。"""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path
from typing import Sequence

import uvicorn

from annotation_app import create_app


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8577


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动本地多属性图像标注界面。")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="图像及 JSON 标注目录（默认：项目下的 10-temp_label）",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"监听地址（默认：{DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认：{DEFAULT_PORT}）")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="启动服务后不自动打开浏览器",
    )
    return parser


def _browser_url(host: str, port: int) -> str:
    browser_host = host
    if host in {"0.0.0.0", "::", "[::]"}:
        browser_host = "127.0.0.1"
    if ":" in browser_host and not browser_host.startswith("["):
        browser_host = f"[{browser_host}]"
    return f"http://{browser_host}:{port}/"


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间。")

    application = create_app(data_dir=args.data_dir, wait_for_initial_scan=False)
    url = _browser_url(args.host, args.port)
    if not args.no_browser:
        timer = threading.Timer(1.0, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()

    print(f"标注界面：{url}")
    print(f"图像目录：{application.state.repository.data_dir}")
    uvicorn.run(application, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
