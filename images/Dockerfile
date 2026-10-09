# One runtime image per baseline, built from GitHub by the harness itself (fetch, then
# build). Each baseline fetches and builds its own pinned checkout revisions.
#   docker build --target runtime --build-arg HARNESS_REF=<commit> --build-arg BASELINE=baseline \
#     -t bip375-interop:baseline -f images/Dockerfile <empty dir>
# Run (artifacts land in the mounted directory):
#   docker run --rm -v "$PWD/artifacts:/harness/artifacts" bip375-interop:baseline \
#     --config baseline/interop.fetched.yaml check --project harness --exhaustive
FROM ubuntu:24.04 AS toolchain
ENV DEBIAN_FRONTEND=noninteractive
# Coldcard's README list (minus gcc-arm-none-eabi), ESP-IDF's prerequisites, qemu-xtensa's
# runtime libraries, libzbar for SeedSigner, and what the Rust and Node installs need.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git python3 python3-pip python3-dev python3-venv python-is-python3 \
      libudev-dev libffi-dev swig libpcsclite-dev autoconf automake libtool pkg-config \
      libsdl2-2.0-0 xterm ca-certificates \
      wget curl xz-utils flex bison gperf cmake ninja-build ccache libssl-dev dfu-util libusb-1.0-0 \
      libgcrypt20 libglib2.0-0 libpixman-1-0 libslirp0 libzbar0 \
    && rm -rf /var/lib/apt/lists/*

# ESP-IDF at the commit Jade's Dockerfile pins, with Espressif's qemu-xtensa.
ARG ESP_IDF_BRANCH=v5.5.5
ARG ESP_IDF_COMMIT=b774170ff46c393eeb5e495ea37936038d3f4f4f
ENV IDF_TOOLS_PATH=/opt/esp IDF_PATH=/opt/esp/idf
RUN git clone -q -b ${ESP_IDF_BRANCH} --depth 1 --recursive --shallow-submodules \
      https://github.com/espressif/esp-idf.git /opt/esp/idf \
    && test "$(git -C /opt/esp/idf rev-parse HEAD)" = "${ESP_IDF_COMMIT}" \
    && /opt/esp/idf/install.sh esp32 \
    && python3 /opt/esp/idf/tools/idf_tools.py install qemu-xtensa

ARG RUST_VERSION=1.97.1
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y -q --profile minimal --default-toolchain ${RUST_VERSION}
ENV PATH=/root/.cargo/bin:$PATH CARGO_TARGET_DIR=/opt/rust-target

# Node 24 (Caravan's engines field) and Bitcoin Core, official tarballs checked by sha256.
ARG NODE_VERSION=24.16.0
ARG BITCOIN_VERSION=31.1
RUN case "$(uname -m)" in \
      aarch64) n=arm64; ns=524659219d6a207a7400f2bde15d19ba060ffbe0d32a8643319ad67e3bb64c78; \
               b=aarch64; bs=dcf1873f2208ba4f962f3398d47e154c39c0084be8f4553e05c940d0ace3d004 ;; \
      x86_64) n=x64; ns=d804845d34eddc21dc1092b519d643ef40b1f58ec5dec5c22b1f4bd8fabde6c9; \
              b=x86_64; bs=b80d9c3e04da78fb6f0569685673418cf686fadba9042d926d13fb87ff503f9e ;; \
    esac \
    && f=node-v${NODE_VERSION}-linux-$n.tar.xz && wget -q https://nodejs.org/dist/v${NODE_VERSION}/$f \
    && echo "$ns  $f" | sha256sum -c - && tar -xJf $f -C /usr/local --strip-components=1 && rm $f \
    && f=bitcoin-${BITCOIN_VERSION}-$b-linux-gnu.tar.gz \
    && wget -q https://bitcoincore.org/bin/bitcoin-core-${BITCOIN_VERSION}/$f \
    && echo "$bs  $f" | sha256sum -c - && tar -xzf $f \
    && install -m 0755 bitcoin-${BITCOIN_VERSION}/bin/bitcoind bitcoin-${BITCOIN_VERSION}/bin/bitcoin-cli /usr/local/bin/ \
    && rm -rf $f bitcoin-${BITCOIN_VERSION}

# The harness from GitHub at a commit, in its own venv with what its workers import.
# SeedSigner's other requirements match the macOS setup; build installs SeedSigner itself.
ARG HARNESS_REPO=https://github.com/macgyver13/bip375-interop.git
ARG HARNESS_REF
RUN test -n "${HARNESS_REF}" \
    && git init -q /harness && git -C /harness fetch -q --depth 1 ${HARNESS_REPO} ${HARNESS_REF} \
    && git -C /harness checkout -q --detach FETCH_HEAD \
    && python3 -m venv /opt/h && /opt/h/bin/pip install -q -e /harness cbor2 pyserial \
         Pillow==12.3.0 qrcode==7.3.1 urtypes==1.0.1 pyzbar==0.1.9
ENV PATH=/opt/h/bin:$PATH

WORKDIR /harness
FROM toolchain AS built
ARG BASELINE=baseline
# README's Ubuntu 24.04 flags, plus -Wno-error=clobbered for arm64 (micropython unix main.c).
ENV CFLAGS_EXTRA="-Wno-error=dangling-pointer -Wno-error=enum-int-mismatch -Wno-error=clobbered"
RUN bip375-interop --config ${BASELINE}/interop.yaml fetch --checkouts-dir /checkouts \
    && bip375-interop --config ${BASELINE}/interop.fetched.yaml build \
    && bip375-interop --config ${BASELINE}/interop.fetched.yaml doctor \
    && cd /checkouts/caravan@* && npm prune --omit=dev --no-audit --no-fund --loglevel=error

# Only what running the harness needs.
FROM ubuntu:24.04 AS runtime-base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      git python3 python3-venv ca-certificates libsdl2-2.0-0 libpcsclite1 libudev1 libffi8 xterm \
      libgcrypt20 libglib2.0-0 libpixman-1-0 libslirp0 libzbar0 \
    && rm -rf /var/lib/apt/lists/*
# libsecp256k1 for coldcard's pysecp256k1 only, kept off the loader path: embit binds the
# pre-0.2 schnorrsig API and segfaults if it finds a system libsecp256k1 >= 0.2.
RUN apt-get update && cd /tmp && apt-get download libsecp256k1-1 \
    && dpkg -x libsecp256k1-1_*.deb /opt/secp && rm libsecp256k1-1_*.deb \
    && ln -s /opt/secp/usr/lib/*/libsecp256k1.so.1 /opt/secp/libsecp256k1.so.1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=built /opt/esp/tools/qemu-xtensa /opt/esp/tools/qemu-xtensa
COPY --from=built /usr/local/bin/node /usr/local/bin/bitcoind /usr/local/bin/bitcoin-cli /usr/local/bin/
COPY --from=built /opt/rust-target/release/spdk-cli /opt/rust-target/release/build_round1 \
     /opt/rust-target/release/finalize /opt/rust-target/release/broadcast_final \
     /opt/rust-target/release/verify_onchain /opt/rust-target/release/
COPY --from=built /opt/h /opt/h
COPY --from=built /checkouts /checkouts
COPY --from=built /harness /harness
ENV IDF_TOOLS_PATH=/opt/esp CARGO_TARGET_DIR=/opt/rust-target SP_DEMO_BIN=/opt/rust-target/release \
    PYSECP_SO=/opt/secp/libsecp256k1.so.1 PATH=/opt/h/bin:$PATH
WORKDIR /harness

FROM runtime-base AS runtime
ENTRYPOINT ["bip375-interop"]
