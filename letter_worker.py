import json
import sys
from pathlib import Path

import joblib
import numpy as np
import sklearn


def main(bundle):
    if sklearn.__version__ != "1.6.1":
        raise RuntimeError("Fingerspelling requires scikit-learn 1.6.1.")
    model = joblib.load(bundle / "fsl_landmark_svm.joblib")
    labels = json.loads((bundle / "labels.json").read_text(encoding="utf-8"))
    if model.n_features_in_ != 63 or model.classes_.tolist() != list(range(26)) or labels != list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        raise ValueError("Fingerspelling model and label map do not match.")
    print(json.dumps({"labels": labels}), flush=True)
    for line in sys.stdin:
        try:
            features = np.asarray(json.loads(line), dtype=np.float32)
            if features.shape != (63,) or not np.isfinite(features).all():
                raise ValueError("Expected 63 finite landmarks.")
            reply = {"probabilities": model.predict_proba(features[None, :])[0].tolist()}
        except (ValueError, TypeError) as error:
            reply = {"error": str(error)}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
