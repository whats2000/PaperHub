# syntax=docker/dockerfile:1
#
# PaperHub frontend image — multi-stage: build the Vite/React SPA, then serve
# the static bundle with nginx. nginx also reverse-proxies the backend API
# (same-origin), so the SPA is built with VITE_API_BASE_URL="" (relative URLs)
# and there is no CORS to configure.

# ── Stage 1: build ──────────────────────────────────────────────────────────
FROM node:20-slim AS build
WORKDIR /app

# Empty base = relative same-origin API calls, proxied by nginx (below).
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ── Stage 2: serve ──────────────────────────────────────────────────────────
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
