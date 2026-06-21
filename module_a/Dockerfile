FROM freqtradeorg/freqtrade:stable_freqai

# stable_freqai already includes lightgbm, scikit-learn, numpy, etc.
# We only add shap (not bundled) and build-essential for its native extension.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir shap==0.45.0

USER ftuser
