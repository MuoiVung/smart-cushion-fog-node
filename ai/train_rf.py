"""
train_rf.py — Random Forest Posture Classifier
================================================
Model   : sklearn RandomForestClassifier
Features: 22 (9 raw FSR + 13 engineered physical features)
Scaler  : None (RF is scale-invariant by design)
CV      : GroupKFold — auto-detects number of subjects from person_XX folders
Output  : ai/models/posture_rf_v<N>.pkl
"""

import os, glob, json, sys, re
import numpy as np
import pandas as pd
import joblib
from sklearn.model_selection import GroupKFold
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score

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
# 1.  FEATURE ENGINEERING  (22 features = 9 raw + 13 physics)
# ═══════════════════════════════════════════════════════════
def extract_features(raw_9: np.ndarray) -> np.ndarray:
    """
    Input : (N, 9) raw FSR values
    Output: (N, 22)  raw + engineered features

    Sensor layout:
        [FL(0), FM(1), FR(2),
         ML(3), MM(4), MR(5),
         BL(6), BM(7), BR(8)]
    """
    f = raw_9.astype(float)
    total = f.sum(axis=1, keepdims=True)
    ts    = np.where(total == 0, 1.0, total).squeeze(1)   # safe denominator

    front = f[:, [0, 1, 2]].sum(1)
    back  = f[:, [6, 7, 8]].sum(1)
    left  = f[:, [0, 3, 6]].sum(1)
    right = f[:, [2, 5, 8]].sum(1)
    mid_r = f[:, [3, 4, 5]].sum(1)

    # Center of Pressure (–1 … +1)
    cop_x = (right - left)  / ts          # positive → lean right
    cop_y = (front - back)  / ts          # positive → lean forward

    # Diagonal asymmetry (cross-leg detection)
    diag_main = f[:, 0] + f[:, 4] + f[:, 8]   # FL + MM + BR
    diag_anti = f[:, 2] + f[:, 4] + f[:, 6]   # FR + MM + BL
    diag_diff = (diag_main - diag_anti) / ts

    # Statistics
    std_v = f.std(1);  max_v = f.max(1);  min_v = f.min(1);  var_v = f.var(1)

    # Normalised regional ratios  (weight-invariant)
    engineered = np.stack([
        cop_x, cop_y, diag_diff,
        std_v, max_v, min_v, var_v,
        front / ts, back / ts,
        left  / ts, right / ts,
        mid_r / ts, f[:, 4] / ts,   # centre-sensor ratio
    ], axis=1)                        # shape (N, 13)

    return np.concatenate([f, engineered], axis=1)   # (N, 22)


# ═══════════════════════════════════════════════════════════
# 2.  DATA PIPELINE
# ═══════════════════════════════════════════════════════════
def _noise_filter(df: pd.DataFrame, noise_thr: int = 20) -> pd.DataFrame:
    """Replicate paper-based quality filter."""
    crop = 20
    if len(df) <= crop * 2:
        return pd.DataFrame()
    df = df.iloc[crop:-crop].copy()
    df = df[(df[FSR_COLS] > noise_thr).sum(axis=1) >= 3]
    if df.empty:
        return df
    tp = df[FSR_COLS].sum(axis=1)
    m  = tp.mean()
    return df[(tp >= m * 0.75) & (tp <= m * 1.25)]


def _subject_id_from_path(file_path: str, folder_map: dict) -> int:
    """
    Return a unique integer group-ID based on the FULL parent folder name.
    Each distinct folder name (e.g. person_01, peter_01, huong_01) gets its
    own ID so they are never merged into the same LOSO fold.
    """
    parent = os.path.basename(os.path.dirname(file_path))
    if parent not in folder_map:
        folder_map[parent] = len(folder_map)
    return folder_map[parent]


def load_dataset(data_folder: str = "./data_exports") -> tuple:
    print(f"\n{'='*55}")
    print(f"  [RF] LOADING DATA FROM: {data_folder}")
    print(f"{'='*55}")

    all_files = sorted(set(
        glob.glob(os.path.join(data_folder, "*.xlsx")) +
        glob.glob(os.path.join(data_folder, "**", "*.xlsx"), recursive=True)
    ))

    X_list, y_list, g_list = [], [], []
    sorted_keys = sorted(FILE_MAP, key=len, reverse=True)
    folder_map: dict = {}   # folder_name → unique int group-ID

    for _, fp in enumerate(all_files):
        fname = os.path.basename(fp).lower()
        if fname.startswith("~$"):       # skip Excel lock files
            continue

        target = next((FILE_MAP[k] for k in sorted_keys if k.lower() in fname), -1)
        if target == -1:
            continue

        try:
            df = pd.read_excel(fp)
        except Exception as e:
            print(f"  ⚠ Skip {os.path.basename(fp)}: {e}")
            continue

        if "Person Present" in df.columns:
            df = df[df["Person Present"] == 1].copy()

        df = _noise_filter(df)
        if df.empty:
            continue

        raw = df[FSR_COLS].values
        X_list.append(extract_features(raw))
        y_list.append(np.full(len(raw), target, dtype=int))
        g_list.append(np.full(len(raw), _subject_id_from_path(fp, folder_map), dtype=int))

        parent = os.path.basename(os.path.dirname(fp))
        print(f"  + [{parent}] {len(raw):>4} frames  class={target}  {os.path.basename(fp)}")

    if not X_list:
        print("❌ No valid data found. Exiting.")
        sys.exit(1)

    X = np.concatenate(X_list)
    y = np.concatenate(y_list)
    g = np.concatenate(g_list)

    subjects = np.unique(g)
    print(f"\n  Total frames  : {len(X)}")
    print(f"  Total subjects: {len(subjects)} → {subjects}")
    print(f"  Features      : {X.shape[1]}  (9 raw + 13 engineered)")
    return X, y, g


# ═══════════════════════════════════════════════════════════
# 3.  TRAINING  (Leave-One-Subject-Out Cross Validation)
# ═══════════════════════════════════════════════════════════
def train(X: np.ndarray, y: np.ndarray, g: np.ndarray):
    n_subjects = len(np.unique(g))
    gkf = GroupKFold(n_splits=n_subjects)

    print(f"\n{'='*55}")
    print(f"  [RF] {n_subjects}-FOLD LEAVE-ONE-SUBJECT-OUT CV")
    print(f"{'='*55}")

    fold_accs = []
    for fold, (tr, te) in enumerate(gkf.split(X, y, groups=g), 1):
        held_out = np.unique(g[te])
        print(f"\n── Fold {fold}  (test subject(s): {held_out}) ──")

        clf = RandomForestClassifier(
            n_estimators=100,    # 100 trees → stable vote, still <2 MB
            max_depth=12,        # deep enough for 22-feat rules; prevents memorising 3 people
            min_samples_split=4, # avoid singleton leaves that overfit
            class_weight="balanced",  # handles any slight class imbalance
            random_state=42,
            n_jobs=-1,
        )
        clf.fit(X[tr], y[tr])
        y_pred = clf.predict(X[te])
        acc = accuracy_score(y[te], y_pred) * 100
        fold_accs.append(acc)

        label_names = [POSTURE_INFO[k]["label"]
                       for k in sorted(np.unique(y[te]))]
        print(f"  Accuracy: {acc:.2f}%")
        print(classification_report(y[te], y_pred, target_names=label_names, zero_division=0))

    print(f"\n{'='*55}")
    print(f"  LOSO CV  →  mean {np.mean(fold_accs):.2f}%  ±  {np.std(fold_accs):.2f}%")
    print(f"{'='*55}")

    # ── Final model on ALL data ──────────────────────────────
    print("\n  Training production model on ALL subjects …")
    final_clf = RandomForestClassifier(
        n_estimators=150,    # slightly more trees for production
        max_depth=14,
        min_samples_split=3,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    final_clf.fit(X, y)

    # Feature importance
    feat_names = FSR_COLS + [
        "CoP_X", "CoP_Y", "Diag_Diff",
        "Std", "Max", "Min", "Var",
        "Front_R", "Back_R", "Left_R", "Right_R", "Mid_R", "Center_R",
    ]
    top = np.argsort(final_clf.feature_importances_)[::-1][:10]
    print("\n  📊 Top-10 Feature Importances:")
    for i in top:
        print(f"     {feat_names[i]:<15} {final_clf.feature_importances_[i]*100:.2f}%")

    return final_clf


# ═══════════════════════════════════════════════════════════
# 4.  SAVE
# ═══════════════════════════════════════════════════════════
def save_model(clf) -> str:
    models_dir = os.path.join("ai", "models")
    os.makedirs(models_dir, exist_ok=True)

    existing = glob.glob(os.path.join(models_dir, "posture_rf_v*.pkl"))
    max_v = max((int(re.search(r"_v(\d+)", f).group(1)) for f in existing
                 if re.search(r"_v(\d+)", f)), default=0)
    path = os.path.join(models_dir, f"posture_rf_v{max_v+1}.pkl")
    joblib.dump(clf, path)
    print(f"\n  ✅ Model saved → {path}")
    return path


# ═══════════════════════════════════════════════════════════
# 5.  INFERENCE HELPER  (called by inference_engine.py)
# ═══════════════════════════════════════════════════════════
def predict(raw_9: list, clf) -> dict:
    """Single-sample inference for the Fog Node."""
    raw = np.array(raw_9, dtype=float).reshape(1, 9)
    feats = extract_features(raw)             # (1, 22)
    probs = clf.predict_proba(feats)[0]
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

    X, y, g = load_dataset(data_folder)
    clf      = train(X, y, g)
    path     = save_model(clf)

    # Quick smoke test
    sample = [2846, 0, 3136, 3503, 0, 2047, 3235, 2220, 2237]
    result = predict(sample, clf)
    print(f"\n  🔍 Smoke test: {json.dumps(result, ensure_ascii=False)}")
