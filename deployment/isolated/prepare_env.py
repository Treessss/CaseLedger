"""Create private, isolated deployment secrets without echoing them to logs."""

import os
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare_env.py LOCAL_ENV TARGET_CONFIG_DIR")

    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    if not source.is_file():
        raise SystemExit("source environment file is missing")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target, 0o700)

    compose_env = target / "compose.env"
    app_env = target / "app.env"
    if compose_env.exists() or app_env.exists():
        raise SystemExit("deployment environment already exists; refusing to overwrite")

    password = secrets.token_urlsafe(36)
    compose_env.write_text(f"CASELEDGER_DB_PASSWORD={password}\n", encoding="utf-8")
    os.chmod(compose_env, 0o600)

    excluded = {
        "DATABASE_URL", "REDIS_URL", "CORS_ORIGINS", "FLASK_DEBUG",
        "EXPOSE_ERROR_DETAILS", "PORT", "HOST", "FLASK_ENV",
    }
    values = dotenv_values(source)
    with app_env.open("w", encoding="utf-8") as handle:
        for key, value in values.items():
            if key in excluded or value is None:
                continue
            if "\n" in value or "\r" in value:
                raise SystemExit(f"multiline environment value is unsupported: {key}")
            handle.write(f"{key}={value}\n")
    os.chmod(app_env, 0o600)
    print("Private deployment environment created; no secret values displayed.")


if __name__ == "__main__":
    main()
