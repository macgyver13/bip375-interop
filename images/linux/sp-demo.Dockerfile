# silent-pay's sp-demo binaries, built from GitHub at the pinned tag. Ubuntu 24.04 so
# the binaries link against the same glibc as the runtime images.
# silent-pay depends on slint unconditionally, so the GUI stack's Linux libraries are
# needed even for these command-line binaries.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git curl ca-certificates pkg-config libssl-dev \
      libfontconfig1-dev libxkbcommon-dev libudev-dev libinput-dev libgbm-dev libseat-dev \
    && rm -rf /var/lib/apt/lists/*
ARG RUST_VERSION=1.97.1
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y -q --profile minimal --default-toolchain ${RUST_VERSION}
ENV PATH=/root/.cargo/bin:$PATH
ARG SP_REPO=https://github.com/macgyver13/silent-pay.git
ARG SP_TAG=bip375-interop/baseline-2026-09-23
RUN git clone -q --depth 1 -b ${SP_TAG} ${SP_REPO} /silent-pay
WORKDIR /silent-pay
RUN cargo build -q --release --locked -p sp-demo \
      --bin build_round1 --bin finalize --bin broadcast_final --bin verify_onchain \
    && mkdir /sp-demo && cp target/release/build_round1 target/release/finalize \
         target/release/broadcast_final target/release/verify_onchain /sp-demo/
