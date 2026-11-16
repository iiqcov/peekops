from core.i18n import MESSAGES, t


def test_every_key_exists_in_every_language():
    en_keys = set(MESSAGES["en"])
    for lang, messages in MESSAGES.items():
        missing = en_keys - set(messages)
        assert not missing, f"{lang} missing keys: {sorted(missing)}"


def test_format_args(lang):
    assert "42" in t("issue_cpu", 42)


def test_unknown_key_returns_key(lang):
    assert t("no_such_key") == "no_such_key"
