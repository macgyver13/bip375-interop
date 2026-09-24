# Jade firmware for qemu, built from GitHub at the pinned tag with ESP-IDF on the build
# platform. Native stand-in for Blockstream's amd64-only jade_builder + Dockerfile.qemu.
FROM ubuntu:24.04 AS idf
ENV DEBIAN_FRONTEND=noninteractive
# ESP-IDF's documented Linux prerequisites, plus qemu-xtensa's runtime libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git wget flex bison gperf python3 python3-pip python3-venv cmake ninja-build ccache \
      libffi-dev libssl-dev dfu-util libusb-1.0-0 ca-certificates \
      libgcrypt20 libglib2.0-0 libpixman-1-0 libsdl2-2.0-0 libslirp0 \
    && rm -rf /var/lib/apt/lists/*
# Jade's Dockerfile pins ESP_IDF_BRANCH=v5.5.5 / ESP_IDF_COMMIT=b774170f.
ARG ESP_IDF_BRANCH=v5.5.5
ARG ESP_IDF_COMMIT=b774170ff46c393eeb5e495ea37936038d3f4f4f
ENV IDF_TOOLS_PATH=/opt/esp
RUN git clone -q -b ${ESP_IDF_BRANCH} --depth 1 --recursive --shallow-submodules \
      https://github.com/espressif/esp-idf.git /opt/esp/idf \
    && test "$(git -C /opt/esp/idf rev-parse HEAD)" = "${ESP_IDF_COMMIT}"
RUN /opt/esp/idf/install.sh esp32 \
    && python3 /opt/esp/idf/tools/idf_tools.py install qemu-xtensa

FROM idf AS jade
# export.sh cannot find itself when sourced from /bin/sh.
ENV IDF_PATH=/opt/esp/idf
ARG JADE_REPO=https://github.com/macgyver13/Jade.git
ARG JADE_TAG=bip375-interop/baseline-2026-09-23
# sources.yaml override: both lines' libwally commits are on the fork's branches, not
# upstream's.
RUN git clone -q --depth 1 -b ${JADE_TAG} ${JADE_REPO} /jade \
    && git -C /jade config submodule.components/libwally-core/upstream.url \
         https://github.com/macgyver13/libwally-core.git \
    && git -C /jade submodule update -q --init --recursive --depth 1
WORKDIR /jade
RUN . /opt/esp/idf/export.sh && pip install -q --require-hashes -r requirements.txt
RUN ./tools/switch_to.sh qemu --dev --ci
# No fwprep.py: its OTA image is unused by qemu, and it names the file after the version,
# where a tag containing "/" becomes a missing subdirectory.
RUN . /opt/esp/idf/export.sh && idf.py all \
    && ./main/qemu/make_flash_img.sh build/flash_image.bin build/qemu_efuse.bin \
    && ls -la build/flash_image.bin build/qemu_efuse.bin && git status --porcelain=v1
