"""Create private local-only Compose configuration; never prints credentials.

Does not run containers, enable cloud inference, or expose a public listener.
Existing files are never replaced. Keep these files outside the repository.
"""

import argparse
import os
import secrets
from pathlib import Path


def prepare(destination: Path) -> None:
    destination = destination.expanduser().resolve()
    root = Path(__file__).resolve().parents[1]
    if destination.is_relative_to(root):
        raise SystemExit("Choose a private configuration directory outside the repository.")
    if "\n" in str(destination) or "\r" in str(destination) or "'" in str(destination):
        raise SystemExit("Configuration directory contains unsupported characters.")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    targets = [destination / name for name in ("database-password", "runtime.env", "compose.env")]
    if any(path.exists() for path in targets):
        raise SystemExit("Configuration already exists; nothing changed.")
    password, operator = secrets.token_hex(32), secrets.token_hex(32)
    values = [
        password + "\n",
        "\n".join(
            [
                f"COUNTERPART_DATABASE_URL=postgresql://counterpart:{password}@postgres:5432/counterpart",
                f"COUNTERPART_OPERATOR_TOKEN={operator}",
            "COUNTERPART_REQUIRE_OPERATOR=true",
            "COUNTERPART_OPERATOR_COOKIE_SECURE=false",
            "COUNTERPART_OPERATOR_LOCAL_HTTP=true",
                'COUNTERPART_TRUSTED_HOSTS=\'["localhost","127.0.0.1","api"]\'',
                'COUNTERPART_ALLOWED_ORIGINS=\'["http://localhost:8080","http://127.0.0.1:8080"]\'',
                "COUNTERPART_LIVE_ENABLED=false",
                "COUNTERPART_EXPORT_ENABLED=false",
                "COUNTERPART_INFERENCE_LIMIT=128",
                "COUNTERPART_STORAGE_BACKEND=filesystem",
                "",
            ]
        ),
        f"COUNTERPART_ENV_FILE='{targets[1]}'\nCOUNTERPART_DB_PASSWORD_FILE='{targets[0]}'\n",
    ]
    for index, path in enumerate(targets):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(values[index])
    print(f"Private configuration created in {destination} (provider calls disabled).")
    print("Use compose.env with docker compose --env-file. Secrets were not printed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path.home() / ".config/counterpart/deploy")
    prepare(parser.parse_args().directory)
