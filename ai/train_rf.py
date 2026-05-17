import os
import glob
import json
import sys
import numpy as np
import pandas as pd
import joblib
import re
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import Normalizer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score

# ========================================================
# 0. POSTURE CONFIGURATION
# ========================================================
POSTURE_INFO = {
    0: {"label": "NUP",  "name": "Neutral Upright Posture"},
    1: {"label": "LF",   "name": "Leaning Forward"},
    2: {"label": "LB",   "name": "Leaning Backward"},
    3: {"label": "LFSR", "name": "Leaning Forward & Support Right"},
    4: {"label": "LFSL", "name": "Leaning Forward & Support Left"},
    5: {"label": "CRL",  "name": "Cross Right Leg"},
    6: {"label": "CLL",  "name": "Cross Left Leg"},
    7: {"label": "CRLL", "name": "Cross Right Thigh"},
    8: {"label": "CLLL", "name": "Cross Left Thigh"}
}

FILE_MAP = {
    "straight": 0, "NUP": 0,
    "leaning_forward": 1, "LF": 1,
    "leaning_backward": 2, "LB": 2,
    "support_right": 3, "LFSR": 3,
    "support_left": 4, "LFSL": 4,
    "cross_right_ankle": 5, "CRL": 5,
    "cross_left_ankle": 6, "CLL": 6,
    "cross_right_knee": 7, "CRLL": 7,
    "cross_left_knee": 8, "CLLL": 8
}

# ========================================================
# 1. DATA PROCESSING PIPELINE
# ========================================================

def prepare_data(data_folder='./data_exports'):
    print(f"--- LOADING DATA FROM {data_folder} ---")
    
    # Support recursive scanning
    all_files = glob.glob(os.path.join(data_folder, "*.xlsx")) + glob.glob(os.path.join(data_folder, "**", "*.xlsx"), recursive=True)
    all_files = sorted(list(set(all_files)))
    
    np.random.seed(42)
    np.random.shuffle(all_files)
    
    fsr_cols = ['FSR Front Left', 'FSR Front Mid', 'FSR Front Right', 
                'FSR Mid Left', 'FSR Mid Mid', 'FSR Mid Right',
                'FSR Back Left', 'FSR Back Mid', 'FSR Back Right']

    X_list, y_list, group_list = [], [], []

    for i, file_path in enumerate(all_files):
        filename = os.path.basename(file_path).lower()
        try:
            df = pd.read_excel(file_path)
        except Exception as e:
            print(f"  + Skip reading error {file_path}: {e}")
            continue
            
        crop_frames = 20
        if len(df) <= crop_frames * 2: continue
        df = df.iloc[crop_frames:-crop_frames].copy()
        
        if 'Person Present' in df.columns:
            df = df[df['Person Present'] == 1]
        
        target_id = -1
        sorted_keys = sorted(FILE_MAP.keys(), key=len, reverse=True)
        for key in sorted_keys:
            if key.lower() in filename:
                target_id = FILE_MAP[key]
                break
        
        if target_id != -1 and not df.empty:
            X_list.append(df[fsr_cols].values)
            y_list.append(np.full(len(df), target_id))
            group_list.append(np.full(len(df), i))
            print(f"  + Loaded {len(df)} frames: {filename} -> Class: {target_id}")

    if not X_list:
        print("❌ ERROR: No valid training data found in directory!")
        sys.exit(1)

    X_all = np.concatenate(X_list)
    y_all = np.concatenate(y_list)
    groups_all = np.concatenate(group_list)
    
    row_sums = X_all.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1
    X_rel = X_all / row_sums

    front_sum = X_rel[:, [0, 1, 2]].sum(axis=1, keepdims=True)
    back_sum = X_rel[:, [6, 7, 8]].sum(axis=1, keepdims=True)
    left_sum = X_rel[:, [0, 3, 6]].sum(axis=1, keepdims=True)
    right_sum = X_rel[:, [2, 5, 8]].sum(axis=1, keepdims=True)
    
    fb_ratio = (front_sum - back_sum) / (front_sum + back_sum + 1e-5)
    lr_ratio = (left_sum - right_sum) / (left_sum + right_sum + 1e-5)
    
    X_features = np.hstack([X_rel, fb_ratio, lr_ratio])
    return X_features, y_all, groups_all

# ========================================================
# 2. TRAINING BLOCK (5-FOLD CV)
# ========================================================

def train_rf_cv(X_all, y_all, groups_all):
    print("\n--- STARTING RANDOM FOREST 5-FOLD CROSS VALIDATION ---")
    skf = GroupKFold(n_splits=min(5, len(np.unique(groups_all))))
    
    fold_no = 1
    acc_per_fold = []
    best_model, best_acc = None, 0.0

    for train_index, test_index in skf.split(X_all, y_all, groups=groups_all):
        X_train, X_test = X_all[train_index], X_all[test_index]
        y_train, y_test = y_all[train_index], y_all[test_index]
        
        rf_model = RandomForestClassifier(n_estimators=100, max_depth=12, random_state=42)
        rf_model.fit(X_train, y_train)
        
        preds = rf_model.predict(X_test)
        acc = accuracy_score(y_test, preds) * 100
        print(f"-> Fold {fold_no} COMPLETED | Accuracy: {acc:.2f}%")
        
        acc_per_fold.append(acc)
        if acc > best_acc:
            best_acc = acc
            best_model = rf_model
            
        fold_no += 1

    print("\n" + "="*50)
    print("SUMMARY OF RANDOM FOREST RESULTS")
    print("="*50)
    print(f"Mean Accuracy: {np.mean(acc_per_fold):.2f}% (+/- {np.std(acc_per_fold):.2f}%)")
    
    print("\n--- FEATURE IMPORTANCES ---")
    fsr_cols = ['FL', 'FM', 'FR', 'ML', 'MM', 'MR', 'BL', 'BM', 'BR']
    feature_names = fsr_cols + ["FB_Ratio", "LR_Ratio"]
    importances = best_model.feature_importances_
    
    for name, imp in zip(feature_names, importances):
        print(f"{name}: {imp*100:.1f}%")

    # --- AUTO VERSIONING & SAVING DIRECTLY IN FOG ---
    models_dir = os.path.join("ai", "models")
    os.makedirs(models_dir, exist_ok=True)
    
    base_name = "posture_rf_model"
    extension = ".pkl"
    
    existing_files = glob.glob(os.path.join(models_dir, f"{base_name}_v*{extension}"))
    max_v = 0
    for f in existing_files:
        match = re.search(r'_v(\d+)', f)
        if match:
            max_v = max(max_v, int(match.group(1)))
    new_v = f"v{max_v + 1}"
    final_filename = os.path.join(models_dir, f"{base_name}_{new_v}{extension}")

    joblib.dump(best_model, final_filename)
    print(f"\n✅ [FOG-AI] Saved Random Forest model directly: {final_filename}")

    return best_model

# ==========================================
# 3. EXECUTION ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    # Use first command line argument as data directory, or default to data_exports
    DATA_FOLDER = './data_exports'
    if len(sys.argv) > 1:
        DATA_FOLDER = sys.argv[1]
        
    X_all, y_all, groups_all = prepare_data(data_folder=DATA_FOLDER)
    best_rf_model = train_rf_cv(X_all, y_all, groups_all)
    
    print("\n--- TEST PREDICTION ---")
    sample_data = np.array([2846, 0, 3136, 3503, 0, 2047, 3235, 2220, 2237])
    sample_rel = sample_data / (sample_data.sum() + 1e-5)
    
    s_front = sample_rel[[0, 1, 2]].sum()
    s_back = sample_rel[[6, 7, 8]].sum()
    s_left = sample_rel[[0, 3, 6]].sum()
    s_right = sample_rel[[2, 5, 8]].sum()
    
    s_fb = (s_front - s_back) / (s_front + s_back + 1e-5)
    s_lr = (s_left - s_right) / (s_left + s_right + 1e-5)
    
    input_final = np.hstack([sample_rel, [s_fb, s_lr]]).reshape(1, -1)
    
    pred_class = best_rf_model.predict(input_final)[0]
    print(f"Predicted Class: {POSTURE_INFO[pred_class]['name']} ({POSTURE_INFO[pred_class]['label']})")
