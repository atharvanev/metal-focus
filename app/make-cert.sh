#!/usr/bin/env bash
# Create a stable, self-signed code-signing identity "Metal Focus Dev" in the login keychain.
# A stable identity keeps macOS privacy grants (Accessibility, Automation) across rebuilds;
# ad-hoc signatures change with every build. Idempotent. macOS asks for your password once.
set -euo pipefail
NAME="Metal Focus Dev"
KC="$HOME/Library/Keychains/login.keychain-db"

if security find-identity -v -p codesigning | grep -q "\"$NAME\""; then
  echo "identity \"$NAME\" already present"; exit 0
fi

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/cert.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
CNF
openssl req -x509 -newkey rsa:2048 -days 3650 -nodes -config "$TMP/cert.cnf" \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" 2>/dev/null
PW=$(openssl rand -hex 16)
openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -name "$NAME" \
  -out "$TMP/id.p12" -passout "pass:$PW" 2>/dev/null
security import "$TMP/id.p12" -k "$KC" -P "$PW" -T /usr/bin/codesign >/dev/null
echo "Trusting \"$NAME\" for code signing (macOS will ask for your password)…"
security add-trusted-cert -r trustRoot -p codeSign -k "$KC" "$TMP/cert.pem"
security find-identity -v -p codesigning | grep "\"$NAME\""
