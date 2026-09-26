import argparse


def main():
    parser = argparse.ArgumentParser(description="Counterpart capture service")
    parser.add_argument("command", choices=["serve", "worker", "init"])
    args = parser.parse_args()
    if args.command == "worker":
        from .worker import main as work

        work()
    else:
        from .config import settings

        config = settings()
        if args.command == "init":
            from .repository import Repository

            Repository(config.database_url).initialize()
        else:
            import uvicorn

            from .api import create_app

            uvicorn.run(create_app(config), host=config.host, port=config.port, access_log=False)
