FROM freqtradeorg/freqtrade:stable

# Install LightGBM and additional dependencies for FreqAI
USER root
RUN pip install --no-cache-dir \
    lightgbm==4.3.0 \
    shap==0.45.0 \
    scikit-learn==1.5.0 \
    asyncpg==0.29.0 \
    httpx==0.27.0

USER ftuser
