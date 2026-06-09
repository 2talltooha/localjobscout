from __future__ import annotations

from localjobscout.url_utils import normalise_adzuna_url, normalise_jobbank_url

# ─── JobBank URL normalisation ────────────────────────────────────────────────


def test_jobbank_strips_jsessionid_from_path() -> None:
    raw = (
        "https://www.jobbank.gc.ca/jobsearch/jobposting/49493721"
        ";jsessionid=395B5E24F65A5B973B6322A9232DB31E.jobsearch75"
        "?source=searchresults"
    )
    result = normalise_jobbank_url(raw)
    assert "jsessionid" not in result
    assert "49493721" in result


def test_jobbank_different_jsessionids_produce_same_url() -> None:
    base = "https://www.jobbank.gc.ca/jobsearch/jobposting/49493721"
    url_a = base + ";jsessionid=ABC123.jobsearch75?source=searchresults"
    url_b = base + ";jsessionid=DEF456.jobsearch76?source=searchresults"
    assert normalise_jobbank_url(url_a) == normalise_jobbank_url(url_b)


def test_jobbank_strips_source_tracking_param() -> None:
    raw = "https://www.jobbank.gc.ca/jobsearch/jobposting/12345?source=searchresults"
    result = normalise_jobbank_url(raw)
    assert "source=" not in result


def test_jobbank_clean_url_unchanged() -> None:
    clean = "https://www.jobbank.gc.ca/jobsearch/jobposting/12345"
    assert normalise_jobbank_url(clean) == clean


def test_jobbank_preserves_path_after_strip() -> None:
    raw = (
        "https://www.jobbank.gc.ca/jobsearch/jobposting/99999"
        ";jsessionid=ZZZZZZ.jobsearch1"
    )
    result = normalise_jobbank_url(raw)
    assert result == "https://www.jobbank.gc.ca/jobsearch/jobposting/99999"


def test_jobbank_mixed_case_jsessionid() -> None:
    raw = (
        "https://www.jobbank.gc.ca/jobsearch/jobposting/11111"
        ";JSESSIONID=AbCdEf.jobsearch2?source=searchresults"
    )
    result = normalise_jobbank_url(raw)
    assert "JSESSIONID" not in result.upper() or "jsessionid" not in result.lower()


# ─── Adzuna URL normalisation ─────────────────────────────────────────────────


def test_adzuna_strips_se_param() -> None:
    raw = "https://www.adzuna.ca/land/ad/12345?se=aDSEjKZU8RGB68X7m2UOqQ&utm_medium=api&utm_source=af839bf6&v=7B9E"
    result = normalise_adzuna_url(raw)
    assert "se=" not in result
    assert "v=" not in result
    assert "utm_medium" not in result
    assert "utm_source" not in result


def test_adzuna_same_job_different_tracking_same_url() -> None:
    base = "https://www.adzuna.ca/land/ad/12345"
    url_a = base + "?se=AAAA&utm_medium=api&utm_source=xxx&v=BBB"
    url_b = base + "?se=CCCC&utm_medium=api&utm_source=yyy&v=DDD"
    assert normalise_adzuna_url(url_a) == normalise_adzuna_url(url_b)


def test_adzuna_clean_details_url_unchanged() -> None:
    clean = "https://www.adzuna.ca/details/12345"
    assert normalise_adzuna_url(clean) == clean


def test_adzuna_preserves_non_tracking_params() -> None:
    url = "https://www.adzuna.ca/details/12345?page=2"
    result = normalise_adzuna_url(url)
    assert "page=2" in result


# ─── Round-trip stability ─────────────────────────────────────────────────────


def test_jobbank_idempotent() -> None:
    raw = (
        "https://www.jobbank.gc.ca/jobsearch/jobposting/12345"
        ";jsessionid=XYZ.jobsearch1?source=searchresults"
    )
    once = normalise_jobbank_url(raw)
    twice = normalise_jobbank_url(once)
    assert once == twice


def test_adzuna_idempotent() -> None:
    raw = "https://www.adzuna.ca/land/ad/12345?se=ABC&v=DEF&utm_medium=api"
    once = normalise_adzuna_url(raw)
    twice = normalise_adzuna_url(once)
    assert once == twice
