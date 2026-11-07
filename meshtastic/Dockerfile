# SPDX-License-Identifier: GPL-3.0-or-later
FROM debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6 AS toolchain
ENV DEBIAN_FRONTEND=noninteractive PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore
RUN apt-get update && apt-get install --no-install-recommends -y \
    g++ git ca-certificates pkg-config python3-pip python3-venv python3-grpc-tools \
    libgpiod-dev libyaml-cpp-dev libbluetooth-dev libi2c-dev libuv1-dev \
    libusb-1.0-0-dev libulfius-dev liborcania-dev libssl-dev libx11-dev \
    libinput-dev libxkbcommon-x11-dev libsqlite3-dev libsdl2-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir platformio==6.1.19
FROM toolchain AS build
WORKDIR /work
RUN git init firmware && cd firmware \
    && git remote add origin https://github.com/meshtastic/firmware.git \
    && git fetch --depth=1 origin 54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb \
    && git checkout --detach FETCH_HEAD && git submodule update --init --recursive
COPY driver /adapter/driver
COPY tools /adapter/tools
COPY COPYING /adapter/COPYING
RUN python3 /adapter/tools/prepare_source.py /work/firmware
WORKDIR /work/firmware
ARG BUILD_JOBS=2
RUN pio run -e native -j "$BUILD_JOBS" \
    && strip --strip-unneeded .pio/build/native/meshtasticd
RUN python3 -m venv /opt/meshtastic-client \
    && /opt/meshtastic-client/bin/pip install --no-cache-dir meshtastic==2.7.11

FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC
RUN apt-get update && apt-get install --no-install-recommends -y \
    ca-certificates python3 libgpiod3 libyaml-cpp0.8 libbluetooth3 libi2c0 \
    libuv1t64 libusb-1.0-0 liborcania2.3 libulfius2.7t64 libssl3t64 \
    libx11-6 libinput10 libxkbcommon-x11-0 libsdl2-2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /etc/meshtasticd/config.d /etc/meshtasticd/available.d /etc/meshtasticd/ssl \
    && chown -R 1000:1000 /data /etc/meshtasticd/ssl
COPY --from=build --chmod=0755 /work/firmware/.pio/build/native/meshtasticd /usr/local/bin/meshtasticd
COPY --from=build /opt/meshtastic-client /opt/meshtastic-client
COPY --from=build /adapter/tools /opt/rn2xx3/tools
LABEL org.opencontainers.image.title="Experimental Meshtastic RN2903 host adapter" \
    org.opencontainers.image.source="https://github.com/akoirala96/RN2xx3_LORAWAN_FIRMWARE" \
    org.opencontainers.image.licenses="GPL-3.0-or-later"
USER 1000:1000
WORKDIR /data
EXPOSE 4403
ENTRYPOINT ["/usr/local/bin/meshtasticd"]
CMD ["--fsdir=/data", "--config=/etc/meshtasticd/config.yaml"]
