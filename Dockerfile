FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv

WORKDIR /app

# Install hash-pinned versions from the lockfile against the default index.
# uv.lock may reference a private mirror, so it is exported instead of synced.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --no-header -o /tmp/requirements.txt \
    && uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY main.py liftline.py ./
COPY liftline-dispatch/ ./liftline-dispatch/

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 2009
CMD ["python", "main.py"]
