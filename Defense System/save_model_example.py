"""
How to save a fitted model (RandomForest or LogisticRegression pipeline) so it
can be reloaded later with load_model_example.py.

joblib.dump() serializes the whole fitted Pipeline object:
  - RandomForestClassifier: every one of the 300 trees' actual structure
    (which feature/threshold each split uses, what each leaf predicts).
  - LogisticRegression: the learned coefficients + intercept, plus the
    fitted StandardScaler's mean_/scale_ (so new data gets scaled the
    same way the training data was).

Requirements: pip install scikit-learn joblib
"""
import joblib

# 1. Train your pipeline as usual, e.g.:
#
#   from sklearn.pipeline import Pipeline
#   from sklearn.impute import SimpleImputer
#   from sklearn.ensemble import RandomForestClassifier
#
#   model = Pipeline([
#       ('imputer', SimpleImputer(strategy='mean')),
#       ('clf', RandomForestClassifier(n_estimators=300, random_state=42)),
#   ])
#   model.fit(X_train, y_train)

# 2. Save it -- this is the part that persists the fitted state to disk.
# Kept in ../models alongside other model versions, named by algorithm:
# RF_... for RandomForest, LR_... for LogisticRegression. Bump the version
# number when saving a retrained model so older versions aren't overwritten.
def save_model(model, path):
    joblib.dump(model, path)
    print(f'Saved fitted model to {path}')


if __name__ == '__main__':
    # Example: assumes `model` was already fit earlier in your session.
    # Pick the path that matches the algorithm you trained:
    save_model(model, '../models/RF_ai_detector_v3.joblib')   # RandomForest
    # save_model(model, '../models/LR_ai_detector_v1.joblib')  # LogisticRegression
