import joblib
import numpy as np
from pathlib import Path

model_path = Path("ai/models/posture_rf_v6.pkl")
if not model_path.exists():
    print(f"Model not found at {model_path}")
else:
    model = joblib.load(model_path)
    print(f"Model loaded: {type(model)}")
    print(f"Classes: {model.classes_}")
    print(f"Num classes: {len(model.classes_)}")
    
    # Dummy prediction
    dummy_input = np.zeros((1, 22))
    probs = model.predict_proba(dummy_input)[0]
    print(f"Probs output: {probs}")
    print(f"Probs length: {len(probs)}")
