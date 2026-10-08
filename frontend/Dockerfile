FROM node:20.20.0-bookworm-slim AS build

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
# Download every package from one stable registry. The lockfile mixes
# registry.npmmirror.com and registry.npmjs.org URLs; from the production host
# the official registry intermittently resets or stalls (EIDLETIMEOUT), which
# breaks `npm ci`. Setting --registry makes npm rewrite the official-registry
# URLs to this host (replace-registry-host=npmjs, the default).
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm ci --registry="$NPM_REGISTRY"
COPY frontend/ ./
ARG VITE_API_BASE_URL=""
ARG BUILD_COMMIT=unknown
ARG BUILD_TIME=unknown
ARG BUILD_DIRTY=unknown
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.29.1-alpine
COPY infra/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/health/live || exit 1
