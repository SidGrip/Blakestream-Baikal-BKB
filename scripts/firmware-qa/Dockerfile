FROM python:3.11-slim-bookworm AS archive-keys
FROM php:5.6-cli

COPY --from=archive-keys /usr/share/keyrings/debian-archive-keyring.gpg /etc/apt/trusted.gpg.d/archive-current.gpg

# Match the legacy interpreters; this image is only for isolated QA.
RUN printf 'deb http://archive.debian.org/debian stretch main\n' > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends python python3 sudo screen procps util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash baikal \
    && useradd -m -u 1001 -s /bin/sh qa-untrusted \
    && printf 'bkb-firmware-qa-v1\n' > /etc/bkb-firmware-qa-container

LABEL io.blakestream.firmware-qa="1"
ENTRYPOINT ["/usr/bin/python3", "/repo/scripts/firmware-qa/test-container.py"]
