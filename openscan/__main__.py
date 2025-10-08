"""Module entry point to support `python -m openscan`.

This simply delegates to the CLI's main function.
"""

from .cli import main


if __name__ == "__main__":
    main()
