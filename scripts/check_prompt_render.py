"""No-LLM render gate: every (subject, phase, language) renders with no leftover
{{...}} tokens, and the heading-localization directive is present for en/ru media
but absent for uz (round-2 localization polish).
Run: uv run python -m scripts.check_prompt_render"""
from app.services.flows import flow_for
from app.services.prompts import get_prompt, _LOCALIZE_HEADINGS_CLAUSE
import re, sys

# representative subjects spanning all families
SUBJECTS = ["matematika", "biologiya", "tarix", "ingliz-tili", "ona-tili"]
LANGS = ["uz", "en", "ru"]
# Derive phases from the live flow (robust to future phase changes); extract has
# no _general prompt file, so skip it.
PHASES = [p for p in flow_for(SUBJECTS[0]) if p != "extract"]
bad = []
combos = 0
for s in SUBJECTS:
    for ph in PHASES:
        for lang in LANGS:
            combos += 1
            body = get_prompt(s, ph, output_language=lang)
            leftover = re.findall(r"\{\{[A-Z_]+\}\}", body)
            if leftover:
                bad.append(f"{s}/{ph}/{lang}: leftover {leftover}")
            has_clause = _LOCALIZE_HEADINGS_CLAUSE in body
            if lang in ("en", "ru") and not has_clause:
                bad.append(f"{s}/{ph}/{lang}: missing heading-localization directive")
            if lang == "uz" and has_clause:
                bad.append(f"{s}/{ph}/{lang}: uz must NOT carry the heading-localization directive")
if bad:
    print("FAIL:\n" + "\n".join(bad)); sys.exit(1)
print(f"RENDER OK: {combos} (subject,phase,language) combos, no leftover tokens, "
      f"heading directive present for en/ru + absent for uz")
