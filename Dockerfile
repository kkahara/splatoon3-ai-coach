FROM node:22-bookworm-slim AS web

WORKDIR /web

COPY web/public/package.json web/public/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/public/index.html web/public/vite.config.js ./
COPY web/public/src ./src
RUN npm run build

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tools ./tools
COPY configs ./configs
COPY calibration/templates ./calibration/templates
COPY --from=web /web/dist ./web/public/dist

RUN pip install --no-cache-dir -e ".[web,cursor]"

# The image must say which commit it was built from: pass the full SHA of the
# commit whose tree is the build context (GIT_SHA=$(git rev-parse HEAD)).
ARG GIT_SHA
RUN echo "$GIT_SHA" | grep -Eqx '[0-9a-f]{40}' \
    || { echo "GIT_SHA build arg must be a full 40-character commit SHA" >&2; exit 1; }
LABEL org.opencontainers.image.revision=$GIT_SHA

ENV PUBLIC_HOST=0.0.0.0 \
    PUBLIC_PORT=8770 \
    PUBLIC_ROOT=/data/public

EXPOSE 8770

CMD ["s3-coach", "public-site", "--port", "8770", "--no-open"]
