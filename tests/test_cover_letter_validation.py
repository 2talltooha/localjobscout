from __future__ import annotations

from localjobscout.cover_letter import validate

_SAMPLE_RESUME = """\
Taha El Ghadi
Waterloo, ON | (519) 498-5872 | tahaelghadi@gmail.com

Profile
First-year Biological Science (Honours) student at the University of Guelph pursuing
a pre-medicine pathway. Seeking part-time positions in research assistance, healthcare
support, pharmacy, or clinical administration.

Certifications
Standard First Aid and CPR
Babysitting Certification
"""


def test_clean_letter_no_warnings() -> None:
    letter = (
        "Dear Hiring Team,\n\n"
        "I am applying for the Lab Assistant position. As a first-year biology "
        "student, I bring strong coursework in cellular biology and general chemistry. "
        "I hold Standard First Aid and CPR certifications.\n\n"
        "Sincerely,\nTaha El Ghadi"
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert warnings == []


def test_whmis_claim_flagged() -> None:
    letter = (
        "I hold Standard First Aid, CPR-C, and WHMIS certifications, "
        "which make me well-suited for lab environments."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert any("WHMIS" in w or "whmis" in w.lower() for w in warnings)


def test_retail_experience_flagged() -> None:
    letter = (
        "My operational reliability from a fast-paced retail role where "
        "customer trust depended on consistency has prepared me well."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert any("retail" in w.lower() for w in warnings)


def test_customer_service_claim_flagged() -> None:
    letter = (
        "My extensive customer service background gives me strong "
        "interpersonal skills relevant to patient-facing roles."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert any("customer service" in w.lower() for w in warnings)


def test_years_lab_experience_flagged() -> None:
    letter = (
        "With 2 years of lab experience, I am comfortable with standard "
        "bench techniques and protocol documentation."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert any("years" in w.lower() and "lab" in w.lower() for w in warnings)


def test_hands_on_lab_experience_flagged() -> None:
    letter = (
        "I bring hands-on lab experience gained through extensive bench work "
        "in molecular biology and PCR techniques."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert any("hands-on" in w.lower() or "lab" in w.lower() for w in warnings)


def test_bare_laboratory_background_flagged() -> None:
    letter = (
        "I have a strong laboratory background that prepares me well "
        "for hands-on scientific work in your facility."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert any("laboratory" in w.lower() for w in warnings)


def test_bare_lab_experience_no_number_flagged() -> None:
    letter = (
        "My lab experience includes working with PCR equipment and "
        "following standard cell culture protocols."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert any("lab" in w.lower() for w in warnings)


def test_bench_experience_flagged() -> None:
    letter = (
        "I bring solid bench experience from my undergraduate research "
        "and am comfortable with routine laboratory procedures."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert any("bench" in w.lower() for w in warnings)


def test_clinical_experience_not_flagged_resume_mentions_clinical() -> None:
    """'clinical' appears in resume (clinical administration), so clinical
    experience claims are not flagged — acceptable false negative."""
    letter = "My clinical experience in healthcare settings is relevant."
    warnings = validate(letter, _SAMPLE_RESUME)
    assert not any("clinical" in w.lower() for w in warnings)


def test_cpr_in_resume_not_flagged() -> None:
    """CPR is in resume, so a CPR claim should not be flagged."""
    letter = (
        "I hold Standard First Aid and CPR certification, making me "
        "prepared for safety-sensitive environments."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    # No false-positive for CPR
    assert not any("cpr" in w.lower() for w in warnings)


def test_multiple_fabrications_multiple_warnings() -> None:
    letter = (
        "I have WHMIS training and retail experience. "
        "I also have 3 years of clinical research experience."
    )
    warnings = validate(letter, _SAMPLE_RESUME)
    assert len(warnings) >= 2


def test_empty_letter_no_warnings() -> None:
    assert validate("", _SAMPLE_RESUME) == []


def test_empty_resume_still_flags_whmis() -> None:
    letter = "I hold WHMIS and CPR certifications."
    warnings = validate(letter, "")
    assert any("whmis" in w.lower() for w in warnings)


# ── master-aware skip: facts that genuinely exist in the reference text pass ──


def test_customer_service_not_flagged_when_in_reference() -> None:
    """When the reference text has customer service (e.g. the new master),
    a customer-service claim is legitimate and must not be flagged."""
    master = "Workplace: Customer service, cash handling, inventory & stocking."
    letter = "My customer service experience prepares me for client-facing work."
    warnings = validate(letter, master)
    assert not any("customer service" in w.lower() for w in warnings)


def test_lab_claim_not_flagged_when_master_has_lab() -> None:
    master = "Laboratory experience: hands-on wet-lab work in General Chemistry."
    letter = "My laboratory experience covers solution preparation."
    warnings = validate(letter, master)
    assert not any("lab" in w.lower() for w in warnings)


def test_years_claim_always_flagged_even_if_lab_in_reference() -> None:
    """Specific years of experience are never legitimate for this applicant,
    even when the reference text mentions lab work."""
    master = "Laboratory experience: hands-on wet-lab work in General Chemistry."
    letter = "I bring 3 years of lab experience to the role."
    warnings = validate(letter, master)
    assert any("years" in w.lower() for w in warnings)
