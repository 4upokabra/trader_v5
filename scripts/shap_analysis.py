"""
SHAP feature importance analysis for the LightGBM model.

Usage (after FreqAI has trained at least one model):
    python scripts/shap_analysis.py --model-dir module_a/user_data/models/trend_lgbm_v1

Outputs:
  - Top features by mean |SHAP|
  - Recommended features to drop (<1% cumulative importance)
  - Bar chart saved to shap_importance.png
"""
from __future__ import annotations

import argparse
import os
import pickle
from pathlib import Path

import numpy as np
import shap


def load_model(model_dir: Path):
    """Load the latest FreqAI LightGBM model from the model directory."""
    # FreqAI stores model as cb_compressed.pkl or lightgbm_compressed.pkl
    candidates = list(model_dir.glob("*.pkl"))
    if not candidates:
        raise FileNotFoundError(f"No .pkl model found in {model_dir}")
    # Pick most recent
    model_path = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"Loading model: {model_path}")
    with open(model_path, "rb") as f:
        return pickle.load(f)


def load_training_data(model_dir: Path) -> tuple[np.ndarray, list[str]]:
    """Load training data saved by FreqAI alongside the model."""
    data_path = model_dir / "training_features.pkl"
    if not data_path.exists():
        raise FileNotFoundError(f"Training features not found at {data_path}")
    with open(data_path, "rb") as f:
        df = pickle.load(f)
    feature_cols = [c for c in df.columns if c.startswith("%-")]
    return df[feature_cols].values, feature_cols


def run_shap(model_dir_str: str) -> None:
    model_dir = Path(model_dir_str)
    model = load_model(model_dir)
    X, feature_names = load_training_data(model_dir)

    # Sample up to 5000 rows for speed
    if len(X) > 5000:
        idx = np.random.choice(len(X), 5000, replace=False)
        X = X[idx]

    print(f"Computing SHAP values for {len(X)} samples × {len(feature_names)} features...")
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)

    # For multi-class output, take mean across classes
    if isinstance(shap_values, list):
        mean_abs = np.mean([np.abs(sv).mean(axis=0) for sv in shap_values], axis=0)
    else:
        mean_abs = np.abs(shap_values).mean(axis=0)

    # Normalize to percentages
    total = mean_abs.sum()
    importance_pct = mean_abs / total * 100

    # Sort descending
    order = np.argsort(importance_pct)[::-1]
    sorted_names = [feature_names[i] for i in order]
    sorted_imp = importance_pct[order]

    print("\n─── SHAP Feature Importance ──────────────────────────────────")
    cumulative = 0.0
    drop_threshold = 1.0  # drop features below 1% cumulative importance
    keep_features = []
    drop_features = []

    for name, imp in zip(sorted_names, sorted_imp):
        cumulative += imp
        marker = "" if imp >= drop_threshold else " ← DROP"
        print(f"  {name:<40} {imp:5.2f}%  (cumul {cumulative:.1f}%){marker}")
        if imp >= drop_threshold:
            keep_features.append(name)
        else:
            drop_features.append(name)

    print(f"\nKeep {len(keep_features)} features, drop {len(drop_features)}")
    if drop_features:
        print("Features to remove from strategy:")
        for f in drop_features:
            print(f"  - {f}")

    # Save chart
    try:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, max(6, len(feature_names) * 0.3)))
        top_n = min(30, len(sorted_names))
        ax.barh(sorted_names[:top_n][::-1], sorted_imp[:top_n][::-1])
        ax.set_xlabel("Mean |SHAP| importance (%)")
        ax.set_title("LightGBM Feature Importance (SHAP)")
        plt.tight_layout()
        out = Path("shap_importance.png")
        plt.savefig(out, dpi=150)
        print(f"\nChart saved to {out.resolve()}")
    except ImportError:
        print("matplotlib not installed — skipping chart")


def main() -> None:
    parser = argparse.ArgumentParser(description="SHAP analysis for FreqAI LightGBM model")
    parser.add_argument(
        "--model-dir",
        default="module_a/user_data/models/trend_lgbm_v1",
        help="Path to FreqAI model directory",
    )
    args = parser.parse_args()
    run_shap(args.model_dir)


if __name__ == "__main__":
    main()
