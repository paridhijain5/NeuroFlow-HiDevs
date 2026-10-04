#!/usr/bin/env bash
# Prints fresh secrets to paste into the Railway dashboard. Nothing is written to disk.
set -euo pipefail
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "REDIS_PASSWORD=$(openssl rand -hex 24)"
echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"
echo "PLUGIN_SECRETS_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
