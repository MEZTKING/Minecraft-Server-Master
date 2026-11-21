import sys

from .cli import main

if __name__ == "__main__":   # terrain worker processes re-import this module; they must not run main()
    sys.exit(main())
