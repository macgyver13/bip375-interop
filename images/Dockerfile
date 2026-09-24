# One runtime image per baseline, built from GitHub by the harness itself (fetch, then
# build). Stages up to runtime-base do not depend on BASELINE, so both baselines' images
# share those layers; each adds only its own coldcard and jade.
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

# The checkouts both locks pin to the same commits, fetched and built once. A config naming
# only them keeps this stage independent of BASELINE.
WORKDIR /harness
FROM toolchain AS shared
ARG SHARED="silent-pay seedsigner caravan spdk embit"
RUN mkdir /shared && cp baseline/interop.lock /shared/ \
    && python3 -c "import sys, yaml; c = yaml.safe_load(open('baseline/interop.yaml')); \
c['checkouts'] = {n: c['checkouts'][n] for n in sys.argv[1:]}; c['artifact_root'] = '/harness/artifacts'; \
yaml.safe_dump(c, open('/shared/interop.yaml', 'w'))" ${SHARED} \
    && bip375-interop --config /shared/interop.yaml fetch --checkouts-dir /checkouts \
    && bip375-interop --config /shared/interop.fetched.yaml build \
    && cd /checkouts/caravan@* && npm prune --omit=dev --no-audit --no-fund --loglevel=error

# The baseline's own coldcard and jade. fetch must reuse every shared checkout: anything
# else fetched here means the two locks disagree on a shared pin.
FROM shared AS devices
ARG BASELINE=baseline
# README's Ubuntu 24.04 flags, plus -Wno-error=clobbered for arm64 (micropython unix main.c).
ENV CFLAGS_EXTRA="-Wno-error=dangling-pointer -Wno-error=enum-int-mismatch -Wno-error=clobbered"
RUN bip375-interop --config ${BASELINE}/interop.yaml fetch --checkouts-dir /checkouts > /tmp/fetched.json \
    && python3 -c "import json; f = set(json.load(open('/tmp/fetched.json'))['fetched']); \
assert f <= {'coldcard', 'jade'}, f" \
    && bip375-interop --config ${BASELINE}/interop.fetched.yaml build coldcard jade \
    && bip375-interop --config ${BASELINE}/interop.fetched.yaml doctor \
    && mkdir /device && mv /checkouts/coldcard@* /checkouts/jade@* /device/

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
COPY --from=shared /opt/esp/tools/qemu-xtensa /opt/esp/tools/qemu-xtensa
COPY --from=shared /usr/local/bin/node /usr/local/bin/bitcoind /usr/local/bin/bitcoin-cli /usr/local/bin/
COPY --from=shared /opt/rust-target/release/spdk-cli /opt/rust-target/release/build_round1 \
     /opt/rust-target/release/finalize /opt/rust-target/release/broadcast_final \
     /opt/rust-target/release/verify_onchain /opt/rust-target/release/
COPY --from=shared /opt/h /opt/h
COPY --from=shared /checkouts /checkouts
COPY --from=shared /harness /harness
ENV IDF_TOOLS_PATH=/opt/esp CARGO_TARGET_DIR=/opt/rust-target SP_DEMO_BIN=/opt/rust-target/release \
    PYSECP_SO=/opt/secp/libsecp256k1.so.1 PATH=/opt/h/bin:$PATH
WORKDIR /harness

FROM runtime-base AS runtime
ARG BASELINE=baseline
COPY --from=devices /device/ /checkouts/
COPY --from=devices /harness/${BASELINE}/interop.fetched.yaml /harness/${BASELINE}/
ENTRYPOINT ["bip375-interop"]
