"""Accuracy gate against hand-labeled real-data TOC rows.

Loads tests/services/fixtures/toc_classifier_labels.json (252 rows, 5 real
Uzbek/Russian math textbooks) and asserts the classifier's predictions
against the hand-labeled ground truth. See the fixture's ``_meta`` for the
documented, accepted false-inclusion allowlist.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from app.services import toc_classifier as tc

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "toc_classifier_labels.json"


def _load_fixture():
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def test_classifier_accuracy_against_real_fixture():
    data = _load_fixture()
    accepted_false_inclusions = {
        (entry["book"], entry["order_index"]) for entry in data["_meta"]["accepted_false_inclusions"]
    }

    total = 0
    correct = 0
    confusion: dict[tuple[str, str], int] = {}
    false_exclusions: list[str] = []
    false_inclusions: set[tuple[str, int]] = set()
    g8geo_predicted_lesson = 0
    g8geo_true_lesson = 0

    for book in data["books"]:
        book_key = book["key"]
        rows = [
            SimpleNamespace(
                section_number=r["section_number"],
                section_title=r["section_title"],
                page_start=r["page_start"],
                page_end=r["page_end"],
            )
            for r in book["rows"]
        ]
        predictions = tc.classify_entries(rows)

        for row, predicted in zip(book["rows"], predictions):
            true_class = row["true_class"]
            order_index = row["order_index"]
            total += 1
            confusion[(true_class, predicted)] = confusion.get((true_class, predicted), 0) + 1
            if predicted == true_class:
                correct += 1

            if true_class == "lesson" and predicted != "lesson":
                false_exclusions.append(f"{book_key} #{order_index} {row['section_title']}")

            if true_class != "lesson" and predicted == "lesson":
                false_inclusions.add((book_key, order_index))

            if book_key == "g8geo":
                if predicted == "lesson":
                    g8geo_predicted_lesson += 1
                if true_class == "lesson":
                    g8geo_true_lesson += 1

    accuracy = correct / total

    print(f"\nAccuracy: {correct}/{total} = {accuracy:.4f}")
    print(f"Confusion matrix (truth, pred) -> count: {confusion}")

    # (a) Zero false-EXCLUSIONS: a true lesson must never be predicted non-lesson.
    assert false_exclusions == [], (
        f"False exclusions (true lesson predicted non-lesson): {false_exclusions}"
    )

    # (b) Overall accuracy >= 0.90.
    assert accuracy >= 0.90, f"Accuracy {accuracy:.4f} below 0.90 threshold"

    # (c) False-INCLUSIONS must be a subset of the documented accepted set.
    unexpected_false_inclusions = false_inclusions - accepted_false_inclusions
    assert unexpected_false_inclusions == set(), (
        f"Unexpected false inclusions not in accepted allowlist: {unexpected_false_inclusions}"
    )

    # (d) G8-Geometriya target: predicted-lesson count must equal true-lesson count (44).
    assert g8geo_predicted_lesson == g8geo_true_lesson, (
        f"g8geo predicted-lesson count {g8geo_predicted_lesson} != true-lesson count {g8geo_true_lesson}"
    )
