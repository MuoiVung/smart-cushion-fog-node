"""
train_tiny_cnn.py — Tiny CNN Posture Classifier
================================================
Model   : Keras Conv2D (spatial 3×3)
Features: 9 raw FSR values  →  reshaped to (3, 3, 1)
Scaler  : L1 Normalizer  → ai/models/scaler_cnn_v<N>.pkl
CV      : GroupKFold — auto-detects number of subjects from person_XX folders
Output  : ai/models/posture_tiny_cnn_v<N>.keras
           ai/models/scaler_cnn_v<N>.pkl

Architecture rationale:
  1 × Conv2D(8 filters, 2×2, padding='same')
    • padding='same' keeps output at 3×3 — no spatial collapse.
    • 8 filters × (2×2×1 weights) = 32 conv params only.
      The original train_v4.py stacked 32/64/128 filters → 38 k params
      on a 9-pixel input → severe overparameterisation.  8 filters is
      the minimum that still detects basic horizontal / vertical pressure
      gradients across the cushion.
  Flatten → Dense(16) → Dropout(0.2) → Dense(9, Softmax)
    • Dense(16) fuses spatial features before classification.
    • Dropout(0.2) prevents the network memorising 3 individuals.

Scaler — L1 Normalizer:
  Each row is divided by its L1-norm (sum of absolute values).
  This makes every sample sum to 1.0 regardless of body weight,
  which is exactly what we want for a weight-agnostic model.
"""

import os, glob, json, sys, re
import numpy as np
import pandas as pd
import joblib

from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import Normalizer

import tensorflow as tf
from tensorflow.keras import layers, models
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

# ═══════════════════════════════════════════════════════════
# 0.  POSTURE CONFIG
# ═══════════════════════════════════════════════════════════
POSTURE_INFO = {
    0: {"label": "NUP",  "name": "Neutral Upright Posture"},
    1: {"label": "LF",   "name": "Leaning Forward"},
    2: {"label": "LB",   "name": "Leaning Backward"},
    3: {"label": "LFSR", "name": "Lean Forward + Support Right"},
    4: {"label": "LFSL", "name": "Lean Forward + Support Left"},
    5: {"label": "CRL",  "name": "Cross Right Leg (Ankle on Knee)"},
    6: {"label": "CLL",  "name": "Cross Left Leg (Ankle on Knee)"},
    7: {"label": "CRLL", "name": "Cross Right Leg (Thigh on Thigh)"},
    8: {"label": "CLLL", "name": "Cross Left Leg (Thigh on Thigh)"},
}

FILE_MAP = {
    "straight": 0, "NUP": 0,
    "leaning_forward": 1, "LF": 1,
    "leaning_backward": 2, "LB": 2,
    "support_right": 3, "LFSR": 3,
    "support_left":  4, "LFSL": 4,
    "cross_right_ankle": 5, "CRL":  5,
    "cross_left_ankle":  6, "CLL":  6,
    "cross_right_knee":  7, "CRLL": 7,
    "cross_left_knee":   8, "CLLL": 8,
}

FSR_COLS = [
    "FSR Front Left", "FSR Front Mid", "FSR Front Right",
    "FSR Mid Left",   "FSR Mid Mid",   "FSR Mid Right",
    "FSR Back Left",  "FSR Back Mid",  "FSR Back Right",
]

# ═══════════════════════════════════════════════════════════
# 1.  DATA PIPELINE  (9 raw features only)
# ═══════════════════════════════════════════════════════════
def _noise_filter(df: pd.DataFrame) -> pd.DataFrame:
    crop = 20
    if len(df) <= crop * 2:
        return pd.DataFrame()
    df = df.iloc[crop:-crop].copy()
    df = df[(df[FSR_COLS] > 20).sum(axis=1) >= 3]
    if df.empty:
        return df
    tp = df[FSR_COLS].sum(axis=1);  m = tp.mean()
    return df[(tp >= m * 0.75) & (tp <= m * 1.25)]


def _subject_id(fp: str, folder_map: dict) -> int:
    """
    Return a unique integer group-ID based on the FULL parent folder name.
    Each distinct folder name (e.g. person_01, peter_01, huong_01) gets its
    own ID so they are never merged into the same LOSO fold.
    """
    parent = os.path.basename(os.path.dirname(fp))
    if parent not in folder_map:
        folder_map[parent] = len(folder_map)
    return folder_map[parent]


def load_dataset(data_folder: str) -> tuple:
    print(f"\n{'='*55}\n  [CNN] LOADING DATA FROM: {data_folder}\n{'='*55}")
    all_files   = sorted(set(
        glob.glob(os.path.join(data_folder, "*.xlsx")) +
        glob.glob(os.path.join(data_folder, "**", "*.xlsx"), recursive=True)
    ))
    sorted_keys = sorted(FILE_MAP, key=len, reverse=True)
    X_list, y_list, g_list = [], [], []
    folder_map: dict = {}   # folder_name → unique int group-ID

    for _, fp in enumerate(all_files):
        fname = os.path.basename(fp).lower()
        if fname.startswith("~$"):
            continue
        target = next((FILE_MAP[k] for k in sorted_keys if k.lower() in fname), -1)
        if target == -1:
            continue
        try:
            df = pd.read_excel(fp)
        except Exception as e:
            print(f"  ⚠ Skip {os.path.basename(fp)}: {e}"); continue

        if "Person Present" in df.columns:
            df = df[df["Person Present"] == 1].copy()
        df = _noise_filter(df)
        if df.empty:
            continue

        raw = df[FSR_COLS].values                     # (N, 9) — raw only
        X_list.append(raw)
        y_list.append(np.full(len(raw), target, dtype=int))
        g_list.append(np.full(len(raw), _subject_id(fp, folder_map), dtype=int))
        print(f"  + [{os.path.basename(os.path.dirname(fp))}] {len(raw):>4} frames  class={target}")

    if not X_list:
        print("❌ No data. Exiting."); sys.exit(1)

    X, y, g = np.concatenate(X_list), np.concatenate(y_list), np.concatenate(g_list)
    print(f"\n  Frames={len(X)}  Subjects={len(np.unique(g))}  Features=9 (raw) → 3×3×1")
    return X, y, g


# ═══════════════════════════════════════════════════════════
# 2.  MODEL ARCHITECTURE
# ═══════════════════════════════════════════════════════════
def build_tiny_cnn() -> tf.keras.Model:
    inp = layers.Input(shape=(3, 3, 1), name="fsr_grid")
    x   = layers.Conv2D(8, (2, 2), padding="same", activation="relu", name="conv1")(inp)
    x   = layers.Flatten(name="flat")(x)
    x   = layers.Dense(16, activation="relu", name="hidden")(x)
    x   = layers.Dropout(0.2, name="dropout")(x)
    out = layers.Dense(9, activation="softmax", name="posture")(x)

    model = models.Model(inp, out, name="TinyCNN_Posture")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.summary()
    return model


# ═══════════════════════════════════════════════════════════
# 3.  TRAINING
# ═══════════════════════════════════════════════════════════
def train(X: np.ndarray, y: np.ndarray, g: np.ndarray):
    n_subj = len(np.unique(g))
    gkf    = GroupKFold(n_splits=n_subj)

    print(f"\n{'='*55}\n  [CNN] {n_subj}-FOLD LEAVE-ONE-SUBJECT-OUT CV\n{'='*55}")

    best_model, best_scaler, best_acc = None, None, 0.0
    fold_accs = []

    callbacks = [
        EarlyStopping(monitor="val_loss", patience=30, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=10, verbose=0),
    ]

    for fold, (tr, te) in enumerate(gkf.split(X, y, groups=g), 1):
        held_out = np.unique(g[te])
        print(f"\n── Fold {fold}  (test subject(s): {held_out}) ──")

        # L1 Normalizer: each row sums to 1 → weight-invariant
        scaler = Normalizer(norm="l1")
        Xtr_sc = scaler.fit_transform(X[tr])
        Xte_sc = scaler.transform(X[te])

        # Reshape for Conv2D: (N, 3, 3, 1)
        Xtr_c = Xtr_sc.reshape(-1, 3, 3, 1)
        Xte_c = Xte_sc.reshape(-1, 3, 3, 1)

        model = build_tiny_cnn()
        model.fit(
            Xtr_c, y[tr],
            validation_data=(Xte_c, y[te]),
            epochs=200, batch_size=32,
            callbacks=callbacks, verbose=0,
        )

        loss, acc = model.evaluate(Xte_c, y[te], verbose=0)
        fold_accs.append(acc * 100)
        print(f"  Accuracy: {acc*100:.2f}%  Loss: {loss:.4f}")

        if acc > best_acc:
            best_acc    = acc
            best_model  = model
            best_scaler = scaler

    print(f"\n  LOSO CV  →  mean {np.mean(fold_accs):.2f}%  ±  {np.std(fold_accs):.2f}%")

    # ── Production model ────────────────────────────────────
    print("\n  Training production model on ALL subjects …")
    prod_scaler = Normalizer(norm="l1")
    Xall = prod_scaler.fit_transform(X).reshape(-1, 3, 3, 1)
    prod_model = build_tiny_cnn()
    prod_model.fit(
        Xall, y,
        epochs=150, batch_size=32,
        callbacks=[EarlyStopping(monitor="loss", patience=20, restore_best_weights=True)],
        verbose=0,
    )
    return prod_model, prod_scaler


# ═══════════════════════════════════════════════════════════
# 4.  SAVE
# ═══════════════════════════════════════════════════════════
def _next_version(d: str, prefix: str, ext: str) -> int:
    existing = glob.glob(os.path.join(d, f"{prefix}_v*.{ext}"))
    return max((int(re.search(r"_v(\d+)", f).group(1)) for f in existing
                if re.search(r"_v(\d+)", f)), default=0) + 1


def save_model(model, scaler) -> tuple:
    d  = os.path.join("ai", "models"); os.makedirs(d, exist_ok=True)
    v  = _next_version(d, "posture_tiny_cnn", "keras")
    mp = os.path.join(d, f"posture_tiny_cnn_v{v}.keras")
    sp = os.path.join(d, f"scaler_cnn_v{v}.pkl")
    model.save(mp)
    joblib.dump(scaler, sp)
    print(f"\n  ✅ Model  → {mp}")
    print(f"  ✅ Scaler → {sp}")
    return mp, sp


# ═══════════════════════════════════════════════════════════
# 5.  INFERENCE HELPER  (for inference_engine.py)
# ═══════════════════════════════════════════════════════════
def predict(raw_9: list, model, scaler) -> dict:
    raw    = np.array(raw_9, dtype=float).reshape(1, 9)
    sc     = scaler.transform(raw).reshape(1, 3, 3, 1)
    probs  = model.predict(sc, verbose=0)[0]
    idx    = int(np.argmax(probs))
    info   = POSTURE_INFO[idx]
    return {
        "posture_id": idx,
        "label": info["label"],
        "posture_name": info["name"],
        "confidence": round(float(probs[idx]), 4),
    }


# ═══════════════════════════════════════════════════════════
# 6.  ENTRY POINT
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    data_folder = sys.argv[1] if len(sys.argv) > 1 else "./data_exports"
    if not os.path.exists(data_folder):
        print(f"❌ Folder not found: {data_folder}"); sys.exit(1)

    X, y, g       = load_dataset(data_folder)
    model, scaler = train(X, y, g)
    save_model(model, scaler)

    sample = [2846, 0, 3136, 3503, 0, 2047, 3235, 2220, 2237]
    result = predict(sample, model, scaler)
    print(f"\n  🔍 Smoke test: {json.dumps(result, ensure_ascii=False)}")
