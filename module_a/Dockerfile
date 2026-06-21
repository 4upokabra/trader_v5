FROM freqtradeorg/freqtrade:stable

# build-essential needed for shap/lightgbm native extensions
# scikit-learn is already bundled with freqtrade — don't reinstall
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    lightgbm==4.3.0 \
    shap==0.45.0 \
    asyncpg==0.29.0 \
    httpx==0.27.0 \
    psycopg2-binary==2.9.9

USER ftuser
