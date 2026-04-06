# AI Model Weights – Place Your Trained Model Here

This directory holds the PyTorch model weight file loaded by `InferenceEngine`.

## Expected filename

```
ai/models/posture_model.pt
```

This path can be overridden with the `MODEL_PATH` environment variable in `.env`.

## Why is this directory empty in the repository?

- Binary model files can be **several hundred MB** and should not be stored in Git.
- Instead, distribute the trained weights separately (e.g., via a GitHub Release,
  Google Drive link, or a model registry such as MLflow / HuggingFace Hub).
- The `*.pt` glob is listed in `.gitignore` to prevent accidental commits.

## What happens without a model file?

The `InferenceEngine` automatically falls back to a **built-in rule-based classifier**
that uses FSR symmetry heuristics to detect common posture deviations.
The rule-based stub works well enough for integration testing and initial demos.

## Training a model

Refer to the project's main documentation or the companion training notebook.
The expected model format is a `PostureMLP` state dict:

```python
# Save
import torch
torch.save(model.state_dict(), "ai/models/posture_model.pt")

# Verify
model = PostureMLP()
model.load_state_dict(torch.load("ai/models/posture_model.pt", map_location="cpu"))
model.eval()
```

### Input / Output spec

| | Detail |
|---|---|
| **Input shape** | `(batch, 4)` – four normalised FSR values in `[0, 1]` |
| **Output shape** | `(batch, 5)` – logits for five posture classes |
| **Classes (index)** | `0=correct, 1=lean_left, 2=lean_right, 3=slouch_forward, 4=lean_back` |
