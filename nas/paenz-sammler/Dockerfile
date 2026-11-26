FROM node:22.21.1-alpine3.22
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY sammler.mjs ./
ENV TZ=Europe/Berlin DATEN=/data NODE_ENV=production
VOLUME /data
CMD ["node", "sammler.mjs"]
