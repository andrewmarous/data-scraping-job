#!/bin/sh
# The installed command uses Python's strict .env loader before CLI startup.
exec python3 -m market_data "$@"
