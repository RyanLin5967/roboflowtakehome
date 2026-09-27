import argparse


def main():
    parser = argparse.ArgumentParser(description="Counterpart capture service")
    parser.add_argument("command", choices=["serve", "worker", "init", "maintenance", "backup"])
    parser.add_argument("--apply", action="store_true", help="Apply maintenance; default reports only")
    parser.add_argument("--destination", help="New private backup directory")
    parser.add_argument("--limit", type=int, default=500, help="Maximum maintenance items per batch")
    parser.add_argument("--after-asset-id", help="Resume a bounded media inventory scan")
    args = parser.parse_args()
    if args.command == "worker":
        from .worker import main as work

        work()
    else:
        from .config import settings

        config = settings()
        if args.command in {"maintenance", "backup"}:
            import json
            from pathlib import Path

            from .lifecycle import create_backup, run_maintenance
            from .object_storage import create_media_store

            media = create_media_store(config)
            try:
                if args.command == "backup":
                    if not args.destination:
                        parser.error("backup requires --destination outside the repository")
                    result = create_backup(config.database_url, media, Path(args.destination))
                else:
                    result = run_maintenance(
                        config.database_url,
                        media,
                        dry_run=not args.apply,
                        limit=args.limit,
                        after_asset_id=args.after_asset_id,
                    )
                print(json.dumps(result, indent=2, default=str))
            finally:
                if hasattr(media, "close"):
                    media.close()
        elif args.command == "init":
            from .repository import Repository

            Repository(config.database_url).initialize()
        else:
            import uvicorn

            from .api import create_app

            if config.host not in {"127.0.0.1", "::1", "localhost"} and not config.require_operator:
                parser.error("A network-bound API requires public mode and HTTPS operator authentication.")
            uvicorn.run(create_app(config), host=config.host, port=config.port, access_log=False)
