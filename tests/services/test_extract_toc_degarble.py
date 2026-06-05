"""Guard: extract_toc instructs the model to repair broken-font title garble.

R10 second manifestation (book 54cd9ff3): a decorative heading font with a
broken cmap makes pypdf extract chapter titles letter-correct but with
scrambled case + injected spaces (KUr SININg e Ng MUh IM -> KURSINING ENG
MUHIM). The fix is a clause in the extract_toc prompt telling the model to
reconstruct such titles. Verified live: gemini de-garbles them from text alone.
"""

import inspect

from app.services import agent


def test_extract_toc_prompt_has_degarble_clause():
    src = inspect.getsource(agent.extract_toc).lower()
    assert "broken pdf font" in src, "extract_toc must warn about broken-font corruption"
    assert "scrambl" in src, "must mention scrambled case"
    assert "reconstruct" in src, "must instruct reconstruction of garbled titles"
