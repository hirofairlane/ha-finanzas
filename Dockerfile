ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apk add --no-cache python3 py3-pip \
    && pip3 install --no-cache-dir --break-system-packages \
        "aiohttp==3.11.11" \
        "python-dateutil==2.9.0.post0" \
        "beautifulsoup4==4.12.3" \
        "lxml==5.3.0"

COPY rootfs /

RUN mkdir -p /data /share/ha_finanzas/inbox /share/ha_finanzas/archive

EXPOSE 8123

LABEL \
    io.hass.name="HA Finanzas" \
    io.hass.description="Personal-finance brain for HA" \
    io.hass.type="addon" \
    io.hass.version="0.1.0"
