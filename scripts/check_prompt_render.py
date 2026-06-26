"""No-LLM render gate: every (subject, phase) renders with no leftover {{...}} tokens.
Run: uv run python -m scripts.check_prompt_render"""
from app.services.flows import flow_for
from app.services.prompts import get_prompt
import re, sys

# representative subjects spanning all families
SUBJECTS = ["matematika", "biologiya", "tarix", "ingliz-tili", "ona-tili"]
# Derive phases from the live flow (robust to future phase changes); extract has
# no _general prompt file, so skip it.
PHASES = [p for p in flow_for(SUBJECTS[0]) if p != "extract"]
bad = []
for s in SUBJECTS:
    for ph in PHASES:
        body = get_prompt(s, ph)
        leftover = re.findall(r"\{\{[A-Z_]+\}\}", body)
        if leftover:
            bad.append(f"{s}/{ph}: leftover {leftover}")
if bad:
    print("FAIL:\n" + "\n".join(bad)); sys.exit(1)
print(f"RENDER OK: {len(SUBJECTS)*len(PHASES)} (subject,phase) combos, no leftover tokens")
