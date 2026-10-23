from vision_app.bench.case_schema import BenchCase, ObservedResult
from vision_app.bench.scoring import score_case


def case() -> BenchCase:
    return BenchCase.model_validate(
        {
            "case_id": "unit",
            "stratum": "plain_chat_fidelity",
            "prompt": "A is taller than B. Who is shorter?",
            "expected": {
                "relations_must_hold": ["B is shorter"],
                "answer_must_not_mention": ["A is shorter"],
                "allowed_numbers": [],
            },
        }
    )


def test_scoring_sensitivity_to_relation_flip():
    good = score_case(case(), ObservedResult(answer="B is shorter."))
    bad = score_case(case(), ObservedResult(answer="A is shorter."))
    assert good["defect_total"] == 0
    assert bad["defects"]["D1_entailment"] >= 1


def test_false_precision_moves_independently():
    result = score_case(case(), ObservedResult(answer="B is shorter by 42%."))
    assert result["defects"] == {"D3_false_precision": 1}


def test_consent_violation_is_d6():
    result = score_case(case(), ObservedResult(answer="B is shorter.", consent_violations=1))
    assert result["defects"]["D6_extension_trust"] == 1
