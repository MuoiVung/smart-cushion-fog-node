"""
train_fnn.py — Hybrid FNN (MLP) Posture Classifier
====================================================
Model   : Keras Dense Network (Feedforward / MLP)
Features: 22 (9 raw FSR + 13 engineered physical features)
Scaler  : StandardScaler  → ai/models/scaler_fnn_v<N>.pkl
CV      : GroupKFold — auto-detects number of subjects from person_XX folders
Output  : ai/models/posture_fnn_v<N>.keras
           ai/models/scaler_fnn_v<N>.pkl

Why 22 features + StandardScaler for FNN?
  - FNN receives a flat 1-D vector → no spatial meaning → use physics features.
  - StandardScaler (mean=0, var=1) → all features contribute equally during
    gradient descent; faster convergence than MinMaxScaler for Gaussian-like
    distributions such as CoP and regional ratios.
"""

import os, glob, json, sys, re
import numpy as np
import pandas as pd
import joblib

from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score

import tensorflow as tf
from tensorflow.keras import layers, models
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau

# Silence TF info/warning spam
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
# 1.  FEATURE ENGINEERING  (identical to train_rf.py)
# ═══════════════════════════════════════════════════════════
def extract_features(raw_9: np.ndarray) -> np.ndarray:
    """
    Input : (N, 9) raw FSR values
    Output: (N, 22) fully weight-invariant features (normalized by total pressure)
    """
    f = raw_9.astype(float)
    total = f.sum(axis=1, keepdims=True)
    ts = np.where(total == 0, 1.0, total) # shape (N, 1)

    # 1. Normalize raw FSR inputs (sum-to-1 per row)
    f_norm = f / ts

    front = f_norm[:, [0, 1, 2]].sum(1)
    back  = f_norm[:, [6, 7, 8]].sum(1)
    left  = f_norm[:, [0, 3, 6]].sum(1)
    right = f_norm[:, [2, 5, 8]].sum(1)
    mid_r = f_norm[:, [3, 4, 5]].sum(1)

    # Center of Pressure (–1 … +1)
    cop_x = right - left
    cop_y = front - back

    # Diagonal asymmetry
    diag_main = f_norm[:, 0] + f_norm[:, 4] + f_norm[:, 8]
    diag_anti = f_norm[:, 2] + f_norm[:, 4] + f_norm[:, 6]
    diag_diff = diag_main - diag_anti

    # Weight-invariant statistics
    std_v = f_norm.std(1)
    max_v = f_norm.max(1)
    min_v = f_norm.min(1)
    var_v = f_norm.var(1)

    # 13 engineered features (all normalized)
    engineered = np.stack([
        cop_x, cop_y, diag_diff,
        std_v, max_v, min_v, var_v,
        front, back,
        left, right,
        mid_r, f_norm[:, 4],
    ], axis=1) # shape (N, 13)

    return np.concatenate([f_norm, engineered], axis=1) # (N, 22)


# ═══════════════════════════════════════════════════════════
# 2.  DATA PIPELINE
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
    print(f"\n{'='*55}\n  [FNN] LOADING DATA FROM: {data_folder}\n{'='*55}")
    all_files  = sorted(set(
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
        X_list.append(extract_features(raw))
        y_list.append(np.full(len(raw), target, dtype=int))
        g_list.append(np.full(len(raw), _subject_id(fp, folder_map), dtype=int))
        print(f"  + [{os.path.basename(os.path.dirname(fp))}] {len(raw):>4} frames  class={target}")

    if not X_list:
        print("❌ No data. Exiting."); sys.exit(1)

    X, y, g = np.concatenate(X_list), np.concatenate(y_list), np.concatenate(g_list)
    print(f"\n  Frames={len(X)}  Subjects={len(np.unique(g))}  Features={X.shape[1]}")
    return X, y, g


# ═══════════════════════════════════════════════════════════
# 3.  MODEL ARCHITECTURE
# ═══════════════════════════════════════════════════════════
def build_fnn() -> tf.keras.Model:
    """
    Architecture rationale:
      Input 22 → Dense 32 → Dropout 0.2 → Dense 16 → Output 9

      • 32 hidden units: large enough to combine CoP + ratio features into
        compound rules; small enough to avoid memorising 3 individuals.
      • Dropout 0.2: randomly zeroes 20 % of activations per mini-batch,
        preventing any single neuron from becoming a 'person identifier'.
      • No BatchNorm needed: StandardScaler already centres each feature.
    """
    inp = layers.Input(shape=(22,), name="fsr_features")
    x   = layers.Dense(32, activation="relu", name="hidden1")(inp)
    x   = layers.Dropout(0.2, name="dropout")(x)
    x   = layers.Dense(16, activation="relu", name="hidden2")(x)
    out = layers.Dense(9,  activation="softmax", name="posture")(x)

    model = models.Model(inp, out, name="FNN_Posture")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


# ═══════════════════════════════════════════════════════════
# 4.  TRAINING
# ═══════════════════════════════════════════════════════════
def train(X: np.ndarray, y: np.ndarray, g: np.ndarray):
    n_subj = len(np.unique(g))
    if n_subj < 2:
        from sklearn.model_selection import StratifiedKFold
        gkf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        print(f"\n  [FNN] Only {n_subj} subject found. Falling back to 5-Fold StratifiedKFold CV.")
    else:
        gkf = GroupKFold(n_splits=n_subj)

    print(f"\n{'='*55}")
    if n_subj < 2:
        print(f"  [FNN] 5-FOLD STRATIFIED K-FOLD CV")
    else:
        print(f"  [FNN] {n_subj}-FOLD LEAVE-ONE-SUBJECT-OUT CV")
    print(f"{'='*55}")

    best_model, best_scaler, best_acc = None, None, 0.0
    fold_accs = []

    callbacks = [
        EarlyStopping(monitor="val_loss", patience=25, restore_best_weights=True),
        ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=10, verbose=0),
    ]

    for fold, (tr, te) in enumerate(gkf.split(X, y, groups=g), 1):
        held_out = np.unique(g[te])
        print(f"\n── Fold {fold}  (test subject(s): {held_out}) ──")

        scaler = StandardScaler()
        Xtr = scaler.fit_transform(X[tr])
        Xte = scaler.transform(X[te])

        model = build_fnn()
        model.fit(
            Xtr, y[tr],
            validation_data=(Xte, y[te]),
            epochs=200, batch_size=32,
            callbacks=callbacks, verbose=0,
        )

        loss, acc = model.evaluate(Xte, y[te], verbose=0)
        fold_accs.append(acc * 100)
        print(f"  Accuracy: {acc*100:.2f}%  Loss: {loss:.4f}")

        if acc > best_acc:
            best_acc    = acc
            best_model  = model
            best_scaler = scaler

    print(f"\n  LOSO CV  →  mean {np.mean(fold_accs):.2f}%  ±  {np.std(fold_accs):.2f}%")

    # ── Production model on all data ────────────────────────
    print("\n  Training production model on ALL subjects …")
    prod_scaler = StandardScaler()
    Xall = prod_scaler.fit_transform(X)
    prod_model = build_fnn()
    prod_model.fit(
        Xall, y,
        epochs=150, batch_size=32,
        callbacks=[EarlyStopping(monitor="loss", patience=20, restore_best_weights=True)],
        verbose=0,
    )
    return prod_model, prod_scaler


# ═══════════════════════════════════════════════════════════
# 5.  SAVE
# ═══════════════════════════════════════════════════════════
def _next_version(models_dir: str, prefix: str, ext: str) -> int:
    existing = glob.glob(os.path.join(models_dir, f"{prefix}_v*.{ext}"))
    return max((int(re.search(r"_v(\d+)", f).group(1)) for f in existing
                if re.search(r"_v(\d+)", f)), default=0) + 1


def save_model(model, scaler) -> tuple:
    d   = os.path.join("ai", "models"); os.makedirs(d, exist_ok=True)
    v   = _next_version(d, "posture_fnn", "keras")
    mp  = os.path.join(d, f"posture_fnn_v{v}.keras")
    sp  = os.path.join(d, f"scaler_fnn_v{v}.pkl")
    model.save(mp)
    joblib.dump(scaler, sp)
    print(f"\n  ✅ Model  → {mp}")
    print(f"  ✅ Scaler → {sp}")
    return mp, sp


# ═══════════════════════════════════════════════════════════
# 6.  INFERENCE HELPER
# ═══════════════════════════════════════════════════════════
def predict(raw_9: list, model, scaler) -> dict:
    raw   = np.array(raw_9, dtype=float).reshape(1, 9)
    feats = extract_features(raw)                     # (1, 22)
    sc    = scaler.transform(feats)                   # (1, 22) standardised
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
# 7.  ENTRY POINT
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    data_folder = sys.argv[1] if len(sys.argv) > 1 else "./data_exports"
    if not os.path.exists(data_folder):
        print(f"❌ Folder not found: {data_folder}"); sys.exit(1)

    X, y, g          = load_dataset(data_folder)
    model, scaler    = train(X, y, g)
    mp, sp           = save_model(model, scaler)

    sample = [2846, 0, 3136, 3503, 0, 2047, 3235, 2220, 2237]
    result = predict(sample, model, scaler)
    print(f"\n  🔍 Smoke test: {json.dumps(result, ensure_ascii=False)}")
