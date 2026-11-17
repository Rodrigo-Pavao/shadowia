"""Linha de comando do ShadowIADetector.

Uso:
    python -m shadowia_detector               # inicia agente + dashboard
    python -m shadowia_detector run --open    # inicia e abre o navegador
    python -m shadowia_detector token         # mostra o token da extensão
    python -m shadowia_detector analyze "texto do prompt"
    python -m shadowia_detector demo          # popula dados de demonstração
    python -m shadowia_detector purge         # aplica política de retenção
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import webbrowser

from . import APP_NAME, __version__
from .config import Config, app_home
from .db import Database
from .service import DetectorService


def _service() -> DetectorService:
    cfg = Config.load()
    db = Database(app_home() / "shadowia.db")
    return DetectorService(cfg, db)


def cmd_run(args) -> int:
    from .netmon import NetworkMonitor
    from .server import create_server

    svc = _service()
    if args.port:
        svc.config.port = args.port
    removed = svc.db.purge(svc.config.retention_days)
    if removed:
        logging.info("Retenção: %d eventos antigos removidos", removed)

    svc.netmon_available = NetworkMonitor.available()
    monitor = None
    if svc.config.network_monitor and not args.no_network:
        monitor = NetworkMonitor(svc, svc.config.network_interval_sec)
        monitor.start()

    httpd = create_server(svc)
    url = f"http://{svc.config.host}:{svc.config.port}/"
    print(f"\n  {APP_NAME} v{__version__}")
    print(f"  Dashboard:        {url}")
    print(f"  Dados:            {app_home()}")
    print(f"  Token extensão:   {svc.config.token}")
    print(f"  Monitor de rede:  {'ativo' if monitor and monitor.available() else 'inativo (instale psutil)' if not svc.netmon_available else 'desativado'}")
    print("  Ctrl+C para encerrar.\n")
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando...")
    finally:
        if monitor:
            monitor.stop()
        httpd.server_close()
        svc.db.close()
    return 0


def cmd_token(args) -> int:
    print(Config.load().token)
    return 0


def cmd_analyze(args) -> int:
    text = args.text if args.text != "-" else sys.stdin.read()
    print(json.dumps(_service().analyze_only(text), indent=2, ensure_ascii=False))
    return 0


def cmd_demo(args) -> int:
    from .demo import seed
    n = seed(_service(), args.count)
    print(f"{n} eventos de demonstração inseridos.")
    return 0


def cmd_purge(args) -> int:
    svc = _service()
    print(f"{svc.db.purge(svc.config.retention_days)} eventos removidos.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shadowia_detector", description=f"{APP_NAME} - monitor local de Shadow AI")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="inicia o agente e o dashboard (padrão)")
    p_run.add_argument("--port", type=int)
    p_run.add_argument("--open", action="store_true", help="abre o dashboard no navegador")
    p_run.add_argument("--no-network", action="store_true", help="não inicia o monitor de rede")
    p_run.set_defaults(func=cmd_run)

    sub.add_parser("token", help="exibe o token usado pela extensão").set_defaults(func=cmd_token)

    p_an = sub.add_parser("analyze", help="analisa um prompt e mostra os riscos")
    p_an.add_argument("text", help="texto do prompt ou '-' para ler da entrada padrão")
    p_an.set_defaults(func=cmd_analyze)

    p_demo = sub.add_parser("demo", help="insere dados de demonstração")
    p_demo.add_argument("--count", type=int, default=300)
    p_demo.set_defaults(func=cmd_demo)

    sub.add_parser("purge", help="remove eventos fora do período de retenção").set_defaults(func=cmd_purge)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not getattr(args, "func", None):
        args = parser.parse_args(["run"] + (argv or sys.argv[1:]))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
