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
COPY web/public/dist ./web/public/dist

RUN pip install --no-cache-dir -e ".[web]"

ENV PUBLIC_HOST=0.0.0.0 \
    PUBLIC_PORT=8770 \
    PUBLIC_ROOT=/data/public

EXPOSE 8770

CMD ["s3-coach", "public-site", "--port", "8770", "--no-open"]
