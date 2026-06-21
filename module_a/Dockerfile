FROM freqtradeorg/freqtrade:stable

# build-essential needed for shap native extension
# asyncpg / psycopg2 intentionally NOT installed here:
#   - asyncpg is only used in shared/ services
#   - overlay state is read from a JSON file (shared volume), not DB
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    lightgbm==4.3.0 \
    shap==0.45.0

USER ftuser
