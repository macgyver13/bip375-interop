# A mixed image plus bitcoind and sp-demo, for scripts/musig2-regtest.sh.
ARG MIXED_IMAGE=bip375-mixed-musig2
FROM bip375-sp-demo AS sp-demo
FROM ${MIXED_IMAGE}
# Bitcoin Core 31.1 release, checked against its SHA256SUMS.
ARG BITCOIN_VERSION=31.1
RUN case "$(uname -m)" in \
      aarch64) arch=aarch64; sum=dcf1873f2208ba4f962f3398d47e154c39c0084be8f4553e05c940d0ace3d004 ;; \
      x86_64) arch=x86_64; sum=b80d9c3e04da78fb6f0569685673418cf686fadba9042d926d13fb87ff503f9e ;; \
    esac \
    && f=bitcoin-${BITCOIN_VERSION}-${arch}-linux-gnu.tar.gz \
    && apt-get update && apt-get install -y --no-install-recommends wget && rm -rf /var/lib/apt/lists/* \
    && wget -q https://bitcoincore.org/bin/bitcoin-core-${BITCOIN_VERSION}/$f \
    && echo "$sum  $f" | sha256sum -c - \
    && tar -xzf $f && install -m 0755 bitcoin-${BITCOIN_VERSION}/bin/bitcoind bitcoin-${BITCOIN_VERSION}/bin/bitcoin-cli /usr/local/bin/ \
    && rm -rf $f bitcoin-${BITCOIN_VERSION}
COPY --from=sp-demo /sp-demo /opt/sp-demo
ENV SP_DEMO_BIN=/opt/sp-demo
