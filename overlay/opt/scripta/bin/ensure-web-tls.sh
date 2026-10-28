#!/bin/sh
# Create the per-device HTTPS identity before lighttpd validates its config.
set -eu

cert_dir=/etc/lighttpd/certs
pem="$cert_dir/lighttpd.pem"
empty_root=/var/empty/blakestream-http
entropy_ready=/run/blakestream-identity-entropy.ready

certificate_is_v3() {
    LC_ALL=C /usr/bin/openssl x509 -in "$1" -noout -text 2>/dev/null |
        LC_ALL=C grep -q '^[[:space:]]*Version: 3 (0x2)$'
}

if [ -L "$cert_dir" ] || [ -L "$pem" ]; then
    echo "refusing symlinked TLS certificate path" >&2
    exit 1
fi
mkdir -p "$cert_dir" "$empty_root"
chown root:root "$cert_dir" "$empty_root"
chmod 0700 "$cert_dir"
chmod 0755 "$empty_root"

if [ -e "$pem" ]; then
    if [ ! -f "$pem" ] || ! /usr/bin/openssl x509 -in "$pem" -noout >/dev/null 2>&1 ||
       ! /usr/bin/openssl rsa -in "$pem" -noout -check >/dev/null 2>&1; then
        echo "existing lighttpd PEM is invalid; refusing to replace it" >&2
        exit 1
    fi
    cert_pub=$(/usr/bin/openssl x509 -in "$pem" -pubkey -noout 2>/dev/null)
    key_pub=$(/usr/bin/openssl rsa -in "$pem" -pubout 2>/dev/null)
    if [ -z "$cert_pub" ] || [ "$cert_pub" != "$key_pub" ]; then
        echo "existing lighttpd certificate and private key do not match" >&2
        exit 1
    fi
    if ! certificate_is_v3 "$pem"; then
        echo "existing lighttpd certificate is not X.509 v3; replace it before starting HTTPS" >&2
        exit 1
    fi
    chown root:root "$pem"
    chmod 0600 "$pem"
    exit 0
fi

# The inherited kernel's /dev/urandom is non-blocking before initialization.
# Generation is permitted only after the identity service has completed its
# blocking /dev/random gate. Existing valid certificates remain bootable.
if [ ! -f "$entropy_ready" ] || [ -L "$entropy_ready" ] ||
   [ "$(stat -c '%u:%a' "$entropy_ready" 2>/dev/null || echo unsafe)" != '0:600' ]; then
    echo "device entropy is not ready for TLS key generation" >&2
    exit 1
fi

umask 077
tmp_dir=$(mktemp -d "$cert_dir/.generate.XXXXXX")
cleanup_tls() {
    rm -f "$tmp_dir/key.pem" "$tmp_dir/request.pem" "$tmp_dir/cert.pem" \
        "$tmp_dir/lighttpd.pem" "$tmp_dir/openssl.cnf" "$tmp_dir/index.txt" \
        "$tmp_dir/index.txt.attr" "$tmp_dir/index.txt.old" "$tmp_dir/serial" \
        "$tmp_dir/serial.old" "$tmp_dir/1000.pem"
    rmdir "$tmp_dir" 2>/dev/null || true
}
trap cleanup_tls EXIT HUP INT TERM

common_name=$(hostname 2>/dev/null | tr -cd 'A-Za-z0-9.-' | cut -c1-63)
[ -n "$common_name" ] || common_name=blakestream-gaintb

# Appliances may boot with a 1970 clock before network time is available. A
# conventional "-days" certificate created then would already be expired when
# the clock corrects. Use an explicit broad validity window and a SAN while
# still generating a unique key locally on every device.
# Explicit certificate extensions also force v3 on the shipped OpenSSL;
# copying CSR extensions alone can incorrectly leave its version at v1.
: > "$tmp_dir/index.txt"
printf '1000\n' > "$tmp_dir/serial"
cat > "$tmp_dir/openssl.cnf" <<EOF
[ ca ]
default_ca = local_ca
[ local_ca ]
database = $tmp_dir/index.txt
new_certs_dir = $tmp_dir
serial = $tmp_dir/serial
private_key = $tmp_dir/key.pem
default_md = sha256
policy = local_policy
unique_subject = no
x509_extensions = server_ext
[ local_policy ]
commonName = supplied
[ req ]
distinguished_name = req_dn
prompt = no
req_extensions = req_ext
[ req_dn ]
commonName = $common_name
[ req_ext ]
subjectAltName = DNS:$common_name
[ server_ext ]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:$common_name
EOF
/usr/bin/openssl req -new -nodes -newkey rsa:2048 -sha256 \
    -config "$tmp_dir/openssl.cnf" -keyout "$tmp_dir/key.pem" \
    -out "$tmp_dir/request.pem" >/dev/null 2>&1
/usr/bin/openssl ca -selfsign -batch -notext -config "$tmp_dir/openssl.cnf" \
    -startdate 20200101000000Z -enddate 20491231235959Z \
    -in "$tmp_dir/request.pem" -out "$tmp_dir/cert.pem" >/dev/null 2>&1
cat "$tmp_dir/key.pem" "$tmp_dir/cert.pem" > "$tmp_dir/lighttpd.pem"
/usr/bin/openssl x509 -in "$tmp_dir/lighttpd.pem" -noout >/dev/null 2>&1
if ! certificate_is_v3 "$tmp_dir/lighttpd.pem"; then
    echo "generated lighttpd certificate is not X.509 v3" >&2
    exit 1
fi
/usr/bin/openssl rsa -in "$tmp_dir/lighttpd.pem" -noout -check >/dev/null 2>&1
cert_pub=$(/usr/bin/openssl x509 -in "$tmp_dir/lighttpd.pem" -pubkey -noout 2>/dev/null)
key_pub=$(/usr/bin/openssl rsa -in "$tmp_dir/lighttpd.pem" -pubout 2>/dev/null)
[ -n "$cert_pub" ] && [ "$cert_pub" = "$key_pub" ]
chown root:root "$tmp_dir/lighttpd.pem"
chmod 0600 "$tmp_dir/lighttpd.pem"
mv "$tmp_dir/lighttpd.pem" "$pem"

trap - EXIT HUP INT TERM
cleanup_tls
exit 0
