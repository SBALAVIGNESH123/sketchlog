ARG NODE_IMAGE=node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293
ARG NGINX_IMAGE=nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de

FROM ${NODE_IMAGE} AS builder
WORKDIR /build
COPY frontend/react-sketchlog/package.json frontend/react-sketchlog/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/react-sketchlog/index.html frontend/react-sketchlog/tsconfig*.json ./
COPY frontend/react-sketchlog/vite*.ts ./
COPY frontend/react-sketchlog/public ./public
COPY frontend/react-sketchlog/src ./src
RUN npm run build:demo

FROM ${NGINX_IMAGE}
COPY demo/nginx.conf /etc/nginx/nginx.conf
COPY --from=builder --chown=101:101 /build/demo-dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=10 \
  CMD ["wget", "-q", "-O", "-", "http://127.0.0.1:8080/healthz"]
CMD ["nginx", "-g", "daemon off;"]
