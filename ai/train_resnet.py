"""
train_resnet.py — Micro ResNet Posture Classifier
==================================================
Model   : Custom Residual Network (1 residual block, no pooling downscale)
Features: 9 raw FSR values  →  reshaped to (3, 3, 1)
Scaler  : L1 Normalizer  → ai/models/scaler_resnet_v<N>.pkl
CV      : GroupKFold — auto-detects number of subjects from person_XX folders
Output  : ai/models/posture_resnet_v<N>.keras
           ai/models/scaler_resnet_v<N>.pkl

Architecture rationale:
  Standard ResNet50/34 cannot run on a 3×3 input because its MaxPooling layers
  would reduce spatial dims to 0.  This 'Micro ResNet' is designed from scratch:

  Stem  : Conv2D(16, 3×3, same) → BN → ReLU
            16 channels extract basic horizontal/vertical gradients.
            'same' padding keeps the output at 3×3.

  Residual Block:
    Shortcut path  : Conv2D(16, 1×1)  — identity projection (no spatial change)
    Main path      : Conv2D(16, 3×3, same) → BN → ReLU →
                     Conv2D(16, 3×3, same) → BN
    Add(shortcut, main) → ReLU
      The skip connection lets the block learn residual corrections rather
      than a full mapping, which stabilises training with small datasets.
      The 1×1 shortcut conv aligns channel dims without changing spatial size.

  GlobalAveragePooling2D  (GAP) instead of Flatten:
    GAP averages each of the 16 feature maps from 3×3 → 1 scalar.
    Result: a 16-D descriptor that is position-invariant.
    This is critical for a cushion where sensor placement may shift slightly
    between uses.  Flatten would encode the absolute position of each value
    and over-fit to that exact physical arrangement.

  Dense(9, Softmax) — direct classification, no intermediate dense layer
    needed because GAP already produced a compact 16-D descriptor.

  NO MaxPooling — would collapse 3×3 → 1×1 immediately.
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
# 1.  DATA PIPELINE
# ═══════════════════════════════════════════════════════════
def _noise_filter(df: pd.DataFrame) -> pd.DataFrame:
    crop = 20
    if len(df) <= crop * 2:
        return pd.DataFrame()
    df = df.iloc[crop:-crop].copy()
    df = df[(df[FSR_COLS] > 20).sum(axis=1) >= 3]
    if df.empty:
        return df
    tp = df[FSR_COLS].sum(axis=1)
    m  = tp.mean()
    df = df[(tp >= m * 0.75) & (tp <= m * 1.25)]
    if df.empty:
        return df

    # Transition noise filter: remove frames that deviate from the stable posture shape
    raw = df[FSR_COLS].values.astype(float)
    row_sums = raw.sum(axis=1, keepdims=True)
    row_sums_safe = np.where(row_sums == 0, 1.0, row_sums)
    normalized = raw / row_sums_safe
    median_posture = np.median(normalized, axis=0)
    distances = np.linalg.norm(normalized - median_posture, axis=1)
    return df[distances <= 0.15]


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
    print(f"\n{'='*55}\n  [ResNet] LOADING DATA FROM: {data_folder}\n{'='*55}")
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

        raw = df[FSR_COLS].values
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
# 2.  MODEL ARCHITECTURE — Micro ResNet
# ═══════════════════════════════════════════════════════════
def residual_block(x: tf.Tensor, filters: int) -> tf.Tensor:
    """One residual block with 1×1 shortcut projection."""
    # Shortcut: project channels to match (no spatial change)
    shortcut = layers.Conv2D(filters, (1, 1), padding="same",
                             use_bias=False, name="shortcut_proj")(x)
    shortcut = layers.BatchNormalization(name="shortcut_bn")(shortcut)

    # Main path
    x = layers.Conv2D(filters, (3, 3), padding="same",
                      use_bias=False, name="res_conv1")(x)
    x = layers.BatchNormalization(name="res_bn1")(x)
    x = layers.Activation("relu", name="res_relu1")(x)

    x = layers.Conv2D(filters, (3, 3), padding="same",
                      use_bias=False, name="res_conv2")(x)
    x = layers.BatchNormalization(name="res_bn2")(x)

    # Add & activate
    x = layers.Add(name="residual_add")([shortcut, x])
    x = layers.Activation("relu", name="res_relu_out")(x)
    return x


def build_micro_resnet() -> tf.keras.Model:
    inp = layers.Input(shape=(3, 3, 1), name="fsr_grid")

    # Stem
    x = layers.Conv2D(16, (3, 3), padding="same",
                      use_bias=False, name="stem_conv")(inp)
    x = layers.BatchNormalization(name="stem_bn")(x)
    x = layers.Activation("relu", name="stem_relu")(x)

    # One residual block (16 channels, keeps 3×3 spatial)
    x = residual_block(x, filters=16)

    # Global Average Pooling: 3×3×16 → 16
    x = layers.GlobalAveragePooling2D(name="gap")(x)

    # Classifier
    out = layers.Dense(9, activation="softmax", name="posture")(x)

    model = models.Model(inp, out, name="MicroResNet_Posture")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(5e-4),   # lower LR for ResNet stability
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.summary(line_length=80)
    return model


# ═══════════════════════════════════════════════════════════
# 3.  TRAINING
# ═══════════════════════════════════════════════════════════
def train(X: np.ndarray, y: np.ndarray, g: np.ndarray):
    n_subj = len(np.unique(g))
    if n_subj < 2:
        from sklearn.model_selection import StratifiedKFold
        gkf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        print(f"\n  [ResNet] Only {n_subj} subject found. Falling back to 5-Fold StratifiedKFold CV.")
    else:
        gkf = GroupKFold(n_splits=n_subj)

    print(f"\n{'='*55}")
    if n_subj < 2:
        print(f"  [ResNet] 5-FOLD STRATIFIED K-FOLD CV")
    else:
        print(f"  [ResNet] {n_subj}-FOLD LEAVE-ONE-SUBJECT-OUT CV")
    print(f"{'='*55}")

    best_model, best_scaler, best_acc = None, None, 0.0
    fold_accs = []

    callbacks = [
        EarlyStopping(monitor="val_loss", patience=35, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=12, verbose=0),
    ]

    for fold, (tr, te) in enumerate(gkf.split(X, y, groups=g), 1):
        held_out = np.unique(g[te])
        print(f"\n── Fold {fold}  (test subject(s): {held_out}) ──")

        scaler = Normalizer(norm="l1")
        Xtr_sc = scaler.fit_transform(X[tr]).reshape(-1, 3, 3, 1)
        Xte_sc = scaler.transform(X[te]).reshape(-1, 3, 3, 1)

        model = build_micro_resnet()
        model.fit(
            Xtr_sc, y[tr],
            validation_data=(Xte_sc, y[te]),
            epochs=250, batch_size=32,
            callbacks=callbacks, verbose=0,
        )

        loss, acc = model.evaluate(Xte_sc, y[te], verbose=0)
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
    prod_model = build_micro_resnet()
    prod_model.fit(
        Xall, y,
        epochs=200, batch_size=32,
        callbacks=[EarlyStopping(monitor="loss", patience=25, restore_best_weights=True)],
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
    v  = _next_version(d, "posture_resnet", "keras")
    mp = os.path.join(d, f"posture_resnet_v{v}.keras")
    sp = os.path.join(d, f"scaler_resnet_v{v}.pkl")
    model.save(mp)
    joblib.dump(scaler, sp)
    print(f"\n  ✅ Model  → {mp}")
    print(f"  ✅ Scaler → {sp}")
    return mp, sp


# ═══════════════════════════════════════════════════════════
# 5.  INFERENCE HELPER
# ═══════════════════════════════════════════════════════════
def predict(raw_9: list, model, scaler) -> dict:
    raw   = np.array(raw_9, dtype=float).reshape(1, 9)
    sc    = scaler.transform(raw).reshape(1, 3, 3, 1)
    probs = model.predict(sc, verbose=0)[0]
    idx   = int(np.argmax(probs))
    info  = POSTURE_INFO[idx]
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
