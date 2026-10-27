"""Support ``python -m nsim`` as well as the installed entry point."""

from nsim.cli import main

raise SystemExit(main())
