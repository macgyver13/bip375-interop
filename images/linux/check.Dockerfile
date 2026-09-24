# Everything `check --exhaustive` needs for the bip375 profile: a mixed image plus
# SeedSigner and the two independent validators (Caravan, spdk) an `evidence` label needs.
# Build from the repo root; the context is spdk-cli's committed sources only (its target/
# holds host binaries):
#   ctx=$(mktemp -d) && git archive HEAD spdk-cli | tar -x -C "$ctx" \
#     && docker build -t bip375-check -f images/linux/check.Dockerfile "$ctx"
ARG BASE_IMAGE=bip375-mixed

# The harness's own spdk-cli wrapper, on the sp-demo image's pinned Rust.
FROM bip375-sp-demo AS spdk-cli
COPY spdk-cli /spdk-cli
WORKDIR /spdk-cli
RUN cargo build -q --release --locked

FROM ${BASE_IMAGE}
# Node 24 (Caravan's engines field), from the official tarball checked by sha256.
ARG NODE_VERSION=24.16.0
RUN case "$(uname -m)" in \
      aarch64) arch=arm64; sum=524659219d6a207a7400f2bde15d19ba060ffbe0d32a8643319ad67e3bb64c78 ;; \
      x86_64) arch=x64; sum=d804845d34eddc21dc1092b519d643ef40b1f58ec5dec5c22b1f4bd8fabde6c9 ;; \
    esac \
    && apt-get update && apt-get install -y --no-install-recommends xz-utils wget \
    && rm -rf /var/lib/apt/lists/* \
    && f=node-v${NODE_VERSION}-linux-$arch.tar.xz \
    && wget -q https://nodejs.org/dist/v${NODE_VERSION}/$f \
    && echo "$sum  $f" | sha256sum -c - \
    && tar -xJf $f -C /usr/local --strip-components=1 && rm $f
# Caravan's pin is only on jvgelder's feat/sp-sending, which can move: fetch the commit.
ARG CARAVAN_REPO=https://github.com/jvgelder/caravan.git
ARG CARAVAN_REV=b3b39754619a9cc06d9c92801927e5639bbd60e7
RUN git init -q /caravan && cd /caravan \
    && git fetch -q --depth 1 ${CARAVAN_REPO} ${CARAVAN_REV} && git checkout -q FETCH_HEAD
# npm install, not npm ci: the pinned package-lock.json is out of sync with package.json.
RUN cd /caravan && npm install --no-audit --no-fund --loglevel=error \
    && npx turbo build --filter=@caravan/psbt... \
    && ls packages/caravan-psbt/dist/index.js && git status --porcelain=v1
# spdk is only checked for its pin; spdk-cli builds the same commit through its git dependency.
ARG SPDK_REPO=https://github.com/macgyver13/spdk.git
ARG SPDK_TAG=bip375-interop/baseline-2026-09-23
RUN git clone -q --depth 1 -b ${SPDK_TAG} ${SPDK_REPO} /spdk
COPY --from=spdk-cli /spdk-cli/target/release/spdk-cli /opt/spdk-cli/spdk-cli
# SeedSigner's pin is on the fork's sp-send-support branch: fetch the commit. check.sh
# installs it into the harness venv without its own embit pin, as on the Mac.
ARG SEEDSIGNER_REPO=https://github.com/macgyver13/seedsigner.git
ARG SEEDSIGNER_REV=4f8442f4012275891f1b4557399e8f62a0226abc
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 \
    && rm -rf /var/lib/apt/lists/* \
    && git init -q /seedsigner && cd /seedsigner \
    && git fetch -q --depth 1 ${SEEDSIGNER_REPO} ${SEEDSIGNER_REV} && git checkout -q FETCH_HEAD
# embit's pin is on the fork's feat/silent-payments-V2 (also upstream PR #145), which can
# move: fetch the commit. check.sh installs it into the harness venv.
ARG EMBIT_REPO=https://github.com/macgyver13/embit.git
ARG EMBIT_REV=f18d23bd5e089693198dcaaa15429040aad6e600
RUN git init -q /embit && cd /embit \
    && git fetch -q --depth 1 ${EMBIT_REPO} ${EMBIT_REV} && git checkout -q FETCH_HEAD
