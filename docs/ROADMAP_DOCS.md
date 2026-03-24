# Feature roadmap documents

| File | Purpose |
|------|---------|
| `FINSAVVY_FEATURE_ROADMAP_FULL.docx` | Full backlog / idea list (no status). |
| `FINSAVVY_FEATURE_ROADMAP_STATUS.docx` | Same structure with **[DONE]** on items implemented in-app. |
| `FINSAVVY_FEATURE_ROADMAP_FULL.rtf` / `..._STATUS.rtf` | Same content; open in Word if you prefer RTF. |

**Regenerate .docx** (from repo root, with venv activated):

```bash
pip install python-docx   # or: pip install -r fin_savvy_app/requirements.txt
python scripts/build_feature_roadmap_docx.py
```

Source of truth for sections: `scripts/build_feature_roadmap_docx.py`.
