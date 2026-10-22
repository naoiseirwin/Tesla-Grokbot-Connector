#!/bin/sh
# Generate the Tesla partner key pair OUTSIDE this repository.
# Publish only the public key. Never commit either file.
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: scripts/generate-keypair.sh DIRECTORY" >&2
  echo "DIRECTORY must be outside this git repository." >&2
  exit 2
fi

parent=$(dirname -- "$1")
name=$(basename -- "$1")
mkdir -p -- "$parent"
parent=$(CDPATH= cd -- "$parent" && pwd)
dest="$parent/$name"

repo=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -n "$repo" ]; then
  case "$dest" in
    "$repo"|"$repo"/*)
      echo "Refusing to write keys inside $repo" >&2
      exit 1
      ;;
  esac
fi

mkdir -p -- "$dest"
umask 077
openssl ecparam -name prime256v1 -genkey -noout -out "$dest/private-key.pem"
openssl ec -in "$dest/private-key.pem" -pubout -out "$dest/public-key.pem"
chmod 600 "$dest/private-key.pem"
chmod 644 "$dest/public-key.pem"
echo "Private key: $dest/private-key.pem"
echo "Public key:  $dest/public-key.pem"
echo "Publish only the public key at:"
echo "  https://<domain>/.well-known/appspecific/com.tesla.3p.public-key.pem"
echo "Do not commit the private key. This server does not sign commands with it."
