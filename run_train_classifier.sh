#!/usr/bin/env bash
# Train local ML classifier from CSV. Run from project root (foobar-it-solutions).
# Uses Docker so no need for a host venv.

set -e
cd "$(dirname "$0")"
echo "Building app image (includes scikit-learn)..."
docker compose build app --quiet
echo "Training from fin_savvy_app/data/labeled_transactions.csv..."
docker compose run --rm app python -m fin_savvy_app.train_classifier
echo "Done. Restart the app to use the new models: docker compose up -d"
