from __future__ import annotations

import numpy as np

def ensemble_stats(models):
    stack = np.stack(models, axis=0).astype(np.float32)
    return np.mean(stack >= 0.6, axis=0).astype(np.float32), np.nanstd(stack, axis=0).astype(np.float32)
