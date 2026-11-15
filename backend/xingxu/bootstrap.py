import argparse
import json
from pathlib import Path
from .auth import AuthService
from .store import JsonRepository
from .deployment import production_directory

def read_accounts(file):
    lines = file.read_text(encoding="utf-8-sig").splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or len(lines) % 2:
        raise ValueError("Credential file requires account/password pairs on consecutive lines")
    return list(zip(lines[::2], lines[1::2]))


def main():
    directory = production_directory()
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=directory)
    args = parser.parse_args()
    args.credentials.chmod(0o600)
    try:
        result = AuthService(JsonRepository(args.data_dir)).import_accounts(read_accounts(args.credentials))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print(json.dumps({"ok": True, **result, "password_storage": "argon2id"}))


if __name__ == "__main__":
    main()
