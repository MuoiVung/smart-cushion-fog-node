import os
import glob
import json
import sys
import numpy as np
import pandas as pd
import joblib
import re
from scipy import stats
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import MinMaxScaler, Normalizer
from tensorflow.keras import layers, models, regularizers
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau

# ========================================================
# 0. POSTURE CONFIGURATION
# ========================================================
POSTURE_INFO = {
    0: {"label": "NUP",  "name": "Neutral Upright Posture", "desc": "Spine is straight and balanced."},
    1: {"label": "LF",   "name": "Leaning Forward", "desc": "Torso leaning forward."},
    2: {"label": "LB",   "name": "Leaning Backward", "desc": "Torso leaning backward."},
    3: {"label": "LFSR", "name": "Leaning Forward & Support Right", "desc": "Leaning forward, resting head/arm on the right desk."},
    4: {"label": "LFSL", "name": "Leaning Forward & Support Left", "desc": "Leaning forward, resting arm on the left desk."},
    5: {"label": "CRL",  "name": "Cross Right Leg (Ankle on Knee)", "desc": "Right ankle resting on the left knee."},
    6: {"label": "CLL",  "name": "Cross Left Leg (Ankle on Knee)", "desc": "Left ankle resting on the right knee."},
    7: {"label": "CRLL", "name": "Cross Right Leg (Thigh on Thigh)", "desc": "Right thigh crossed over the left thigh."},
    8: {"label": "CLLL", "name": "Cross Left Leg (Thigh on Thigh)", "desc": "Left thigh crossed over the right thigh."}
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
# 1. DATA PROCESSING PIPELINE (PAPER-BASED)
# ========================================================

def paper_based_filter(df, noise_threshold=20):
    fsr_cols = ['FSR Front Left', 'FSR Front Mid', 'FSR Front Right', 
                'FSR Mid Left', 'FSR Mid Mid', 'FSR Mid Right',
                'FSR Back Left', 'FSR Back Mid', 'FSR Back Right']
    
    crop_frames = 20
    if len(df) <= crop_frames * 2:
        return pd.DataFrame() 
    
    df_clean = df.iloc[crop_frames:-crop_frames].copy()
    
    active_sensors_count = (df_clean[fsr_cols] > noise_threshold).sum(axis=1)
    df_clean = df_clean[active_sensors_count >= 3]
    
    if df_clean.empty:
        return df_clean
        
    total_pressure = df_clean[fsr_cols].sum(axis=1)
    mean_tp = total_pressure.mean()
    lower_bound = mean_tp * 0.75 
    upper_bound = mean_tp * 1.25 
    
    df_clean = df_clean[(total_pressure >= lower_bound) & (total_pressure <= upper_bound)]
    return df_clean

def prepare_data_pipeline_paper(data_folder='./data_exports', FILE_MAP=None):
    print(f"--- STARTING DATA PIPELINE ON: {data_folder} ---")
    
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
            
        if 'Person Present' in df.columns:
            df = df[df['Person Present'] == 1].copy()
        
        df = paper_based_filter(df)
        
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
            print(f"  + Filtered & retained {len(df)} frames: {filename} -> Class: {target_id}")

    if not X_list:
        print("❌ ERROR: No valid training data found in directory!")
        sys.exit(1)
        
    X_all = np.concatenate(X_list)
    y_all = np.concatenate(y_list)
    groups_all = np.concatenate(group_list)
    print(f"\n=> TOTAL VALID FRAMES FILTERED: {len(X_all)}")
    return X_all, y_all, groups_all

# ========================================================
# 2. TRAINING BLOCK (NEW ARCHITECTURE + 5-FOLD CV)
# ========================================================

def build_cnn_model():
    model = models.Sequential([
        layers.Input(shape=(3, 3, 1)),
        layers.GaussianNoise(0.002), 
        
        layers.Conv2D(32, (2, 2), padding='same'),
        layers.BatchNormalization(),
        layers.Activation('relu'),
        
        layers.Conv2D(64, (2, 2), padding='same'),
        layers.BatchNormalization(),
        layers.Activation('relu'),

        layers.Conv2D(128, (2, 2), padding='same'),
        layers.BatchNormalization(),
        layers.Activation('relu'),
        
        layers.Flatten(),
        layers.Dense(128, kernel_regularizer=regularizers.l2(0.01)),
        layers.BatchNormalization(),
        layers.Activation('relu'),
        layers.Dropout(0.4),
        
        layers.Dense(9, activation='softmax')
    ])
    model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])
    return model

def train_5_fold_cv(X_all, y_all, groups_all):
    print("\n--- STARTING 5-FOLD CROSS VALIDATION TRAINING (GROUP-BASED) ---")
    skf = GroupKFold(n_splits=min(5, len(np.unique(groups_all))))
    
    fold_no = 1
    acc_per_fold, loss_per_fold = [], []
    best_model, best_scaler, best_acc = None, None, 0.0

    for train_index, test_index in skf.split(X_all, y_all, groups=groups_all):
        print(f"\n=================================")
        print(f"   RUNNING FOLD {fold_no}")
        print(f"=================================")
        
        X_train_raw, X_test_raw = X_all[train_index], X_all[test_index]
        y_train, y_test = y_all[train_index], y_all[test_index]
        
        scaler = Normalizer(norm='l1')
        X_train_scaled = scaler.fit_transform(X_train_raw)
        X_test_scaled = scaler.transform(X_test_raw)
        
        X_train = X_train_scaled.reshape(-1, 3, 3, 1)
        X_test = X_test_scaled.reshape(-1, 3, 3, 1)
        
        model = build_cnn_model()
        early_stop = EarlyStopping(monitor='val_loss', patience=30, restore_best_weights=True)
        reduce_lr = ReduceLROnPlateau(monitor='val_loss', factor=0.5, patience=10)
        
        model.fit(
            X_train, y_train, epochs=200, batch_size=32, 
            validation_data=(X_test, y_test), callbacks=[early_stop, reduce_lr], verbose=1
        )
        
        scores = model.evaluate(X_test, y_test, verbose=0)
        print(f"-> Fold {fold_no} COMPLETED | Loss: {scores[0]:.4f} | Accuracy: {scores[1]*100:.2f}%")
        
        acc_per_fold.append(scores[1] * 100)
        loss_per_fold.append(scores[0])
        
        if scores[1] > best_acc:
            best_acc = scores[1]
            best_model = model
            best_scaler = scaler
            
        fold_no += 1

    # --- FALLBACK FOR EXTREMELY SMALL / SINGLE-CLASS DATASETS ---
    if best_model is None:
        print("\n⚠️ WARNING: All folds resulted in 0.0% validation accuracy (often due to small/imbalanced datasets). Using the last fold's model as a fallback.")
        best_model = model
        best_scaler = scaler

    print("\n" + "="*50)
    print("SUMMARY OF 5-FOLD CROSS VALIDATION RESULTS")
    print("="*50)
    print(f"Mean Accuracy: {np.mean(acc_per_fold):.2f}% (Standard Deviation: +/- {np.std(acc_per_fold):.2f}%)")
    print(f"Mean Loss:     {np.mean(loss_per_fold):.4f}")
    
    # --- AUTO VERSIONING & SAVING DIRECTLY IN FOG ---
    models_dir = os.path.join("ai", "models")
    os.makedirs(models_dir, exist_ok=True)
    
    model_base = "posture_9_model_mix_paper"
    scaler_base = "fsr_scaler_9_mix_paper"
    
    # Find next version from the models folder
    existing_models = glob.glob(os.path.join(models_dir, f"{model_base}_v*.h5"))
    max_v = 0
    for f in existing_models:
        match = re.search(r'_v(\d+)', f)
        if match: max_v = max(max_v, int(match.group(1)))
    
    new_v = f"v{max_v + 1}"
    model_path = os.path.join(models_dir, f"{model_base}_{new_v}.h5")
    scaler_path = os.path.join(models_dir, f"{scaler_base}_{new_v}.pkl")

    best_model.save(model_path)
    joblib.dump(best_scaler, scaler_path)
    print(f"\n✅ [FOG-AI] Saved best model directly: {model_path} & {scaler_path}")
    
    return best_model, best_scaler

# ========================================================
# 3. INFERENCE BLOCK
# ========================================================

def predict_posture_9(raw_fsr_values, model, scaler):
    if isinstance(raw_fsr_values, np.ndarray):
        raw_fsr_values = raw_fsr_values.tolist()
    else:
        cleaned_values = []
        for x in raw_fsr_values:
            if isinstance(x, np.generic): cleaned_values.append(x.item())
            else: cleaned_values.append(x)
        raw_fsr_values = cleaned_values

    input_norm = scaler.transform([raw_fsr_values])
    pred_probs = model.predict(input_norm.reshape(1, 3, 3, 1), verbose=0)[0]
    
    class_id = np.argmax(pred_probs).item() 
    info = POSTURE_INFO[class_id]
    
    result = {
        "input_sensors": {
            "Front": raw_fsr_values[0:3],
            "Mid":   raw_fsr_values[3:6],
            "Back":  raw_fsr_values[6:9]
        },
        "ai_output": {
            "posture_id": class_id,
            "label": info["label"],
            "posture_name": info["name"],
            "confidence": round(float(pred_probs[class_id]), 4)
        }
    }
    return result

# ==========================================
# 4. EXECUTION ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    print("=== STARTING LOCAL CNN AI TRAINING PIPELINE ===")

    # Use first command line argument as data directory, or default to data_exports
    DATA_FOLDER = './data_exports'
    if len(sys.argv) > 1:
        DATA_FOLDER = sys.argv[1]
        
    if not os.path.exists(DATA_FOLDER):
        print(f"❌ ERROR: Training folder {DATA_FOLDER} not found!")
        sys.exit(1)
        
    print(f"--- TRAINING DATA SOURCE: {DATA_FOLDER} ---")
    X_all, y_all, groups_all = prepare_data_pipeline_paper(data_folder=DATA_FOLDER, FILE_MAP=FILE_MAP)
    
    # 5-Fold Cross Validation Training
    best_model, best_scaler = train_5_fold_cv(X_all, y_all, groups_all)
    
    print("\n--- SINGLE SAMPLE PREDICTION TEST ---")
    sample_data = [2846, 0, 3136, 3503, 0, 2047, 3235, 2220, 2237] 
    result = predict_posture_9(sample_data, best_model, best_scaler)
    print(json.dumps(result, indent=4, ensure_ascii=False))
