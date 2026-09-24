# Jade (ESP-IDF + qemu) plus the built Coldcard tree, for mixed-device scenarios.
ARG JADE_IMAGE=bip375-jade
ARG COLDCARD_IMAGE=bip375-coldcard
FROM ${COLDCARD_IMAGE} AS coldcard
FROM ${JADE_IMAGE}
# Coldcard's runtime libraries (simulator, ckcc-protocol).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpcsclite1 libudev1 libffi8 xterm \
    && rm -rf /var/lib/apt/lists/*
# libsecp256k1 for Coldcard's pysecp256k1 only, kept off the loader path: embit binds the
# pre-0.2 schnorrsig API and segfaults if it finds a system libsecp256k1 >= 0.2.
RUN apt-get update && cd /tmp && apt-get download libsecp256k1-1 \
    && dpkg -x libsecp256k1-1_*.deb /opt/secp && rm libsecp256k1-1_*.deb \
    && ln -s /opt/secp/usr/lib/*/libsecp256k1.so.1 /opt/secp/libsecp256k1.so.1 \
    && rm -rf /var/lib/apt/lists/*
ENV PYSECP_SO=/opt/secp/libsecp256k1.so.1
COPY --from=coldcard /cc /cc
