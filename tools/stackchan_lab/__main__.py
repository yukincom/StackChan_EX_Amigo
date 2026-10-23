from server.security_runtime import require_secure_environment

require_secure_environment()

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
