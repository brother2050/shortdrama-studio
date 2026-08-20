"""`python -m app` 启动入口。"""
from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="shortdrama-studio",
                                     description="完全离线的对话式连续短剧生成平台")
    parser.add_argument("--host", default=os.environ.get("STUDIO_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("STUDIO_PORT", "8320")))
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()
    import uvicorn
    uvicorn.run("app.main:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()
