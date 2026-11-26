# Basisimage per Digest gepinnt (Tag 22.21.1-alpine3.22). Aktualisieren: Digest neu abfragen und hier eintragen.
FROM node:22.21.1-alpine3.22@sha256:b2358485e3e33bc3a33114d2b1bdb18cdbe4df01bd2b257198eb51beb1f026c5
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY sammler.mjs ./
# SAMMLER_UID/GID: Der Sammler startet als root, übergibt /data an diesen Benutzer ("node", 1000)
# und läuft danach nur noch mit dessen Rechten. Grund: data/ auf der Synology gehört oft root.
ENV TZ=Europe/Berlin DATEN=/data NODE_ENV=production SAMMLER_UID=1000 SAMMLER_GID=1000
VOLUME /data
CMD ["node", "sammler.mjs"]
