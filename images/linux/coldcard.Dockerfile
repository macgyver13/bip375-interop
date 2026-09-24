# Coldcard unix simulator, built from GitHub at the pinned tag.
FROM ubuntu:24.04 AS deps
ENV DEBIAN_FRONTEND=noninteractive
# Coldcard README's Ubuntu list, minus gcc-arm-none-eabi (device firmware only),
# plus libsecp256k1 for the tests' pysecp256k1 and libsdl2 for the simulator.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git python3 python3-pip python3-dev python3-venv python-is-python3 \
      libudev-dev libffi-dev swig libpcsclite-dev autoconf automake libtool pkg-config \
      libsecp256k1-dev libsdl2-2.0-0 xterm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

FROM deps
ARG CC_REPO=https://github.com/macgyver13/coldcard-firmware.git
ARG CC_TAG=bip375-interop/baseline-2026-09-23
RUN git clone -q --depth 1 -b ${CC_TAG} ${CC_REPO} /cc
WORKDIR /cc
# Only what the unix simulator needs: libngu's esp-idf and mpy, and micropython's MCU and
# Bluetooth libraries, are left out (lwip's savannah host cannot serve a shallow clone).
# sources.yaml overrides: these submodule commits are not on the upstream repos' refs.
RUN git config submodule.external/ckcc-protocol.url https://github.com/macgyver13/ckcc-protocol.git \
    && git config submodule.external/libngu.url https://github.com/macgyver13/libngu.git \
    && git submodule update -q --init --depth 1 \
         external/ckcc-protocol external/libngu external/micropython external/mpy-qr \
    && git -C external/libngu submodule update -q --init --depth 1 libs/bech32 libs/cifra libs/secp256k1 \
    && git -C external/micropython submodule update -q --init --depth 1 \
         lib/axtls lib/berkeley-db-1.xx lib/libffi lib/libhydrogen lib/mbedtls
RUN python3 -m venv ENV && ENV/bin/pip install -q -U pip setuptools \
    && ENV/bin/pip install -q -r requirements.txt && ENV/bin/pip install -q pysdl2-dll
# README's Ubuntu 24.04 flags, plus -Wno-error=clobbered for arm64 (MicroPython unix main.c).
# unix/Makefile derives VARIANT_DIR from $(PWD), so its steps run inside unix/.
ENV MPY_CFLAGS="-Wno-error=dangling-pointer -Wno-error=enum-int-mismatch -Wno-error=clobbered"
RUN make -C external/micropython/mpy-cross CFLAGS_EXTRA="$MPY_CFLAGS" -j6 \
    && cd unix && make setup CFLAGS_EXTRA="$MPY_CFLAGS" && make ngu-setup \
    && make CFLAGS_EXTRA="$MPY_CFLAGS" -j6 \
    && cd .. && git rev-parse HEAD && git status --porcelain=v1
