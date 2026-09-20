"""命令行入口：python -m glyphscope --host ... --port ..."""
import argparse

from .service import Service
from .webapp import make_server


def main(argv=None):
    parser = argparse.ArgumentParser(prog="glyphscope",
                                     description="字形子集检查站")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5230)
    parser.add_argument("--home", default=None,
                        help="数据目录（默认 ./.glyphscope）")
    args = parser.parse_args(argv)

    service = Service()
    httpd = make_server(args.host, args.port, service)
    print("字形子集检查站已启动： http://%s:%d" % (args.host, args.port))
    print("数据目录： %s" % service.storage.root)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
