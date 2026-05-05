from __future__ import annotations

import numpy as np

def ops_feasibility(prob):
    return (np.asarray(prob) > 0.58).astype(np.uint8)
