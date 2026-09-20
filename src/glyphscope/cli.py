"""命令行入口：``python -m glyphscope --host 127.0.0.1 --port 5230``。"""

import argparse

from .web.app import create_app


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="glyphscope", description="字形子集检查站本地服务"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5230)
    parser.add_argument("--home", default=None, help="存储目录（默认 ./.glyphscope）")
    args = parser.parse_args(argv)
    app = create_app(args.home)
    print("字形子集检查站：http://%s:%d" % (args.host, args.port))
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
