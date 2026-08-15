"""Allow ``python -m volante`` as an alias for the ``volante`` console script."""

from volante.cli import main

raise SystemExit(main())