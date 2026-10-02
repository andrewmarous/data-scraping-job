#!/bin/sh
# Python reads the private .env file. Never source it as shell code.
set -eu
unset COLLECTOR_TOKEN
exec "$1" -m collector --config "$2" collect
