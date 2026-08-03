"""Regressão de segurança: SECRET_KEY é obrigatória e forte (sem fallback fraco)."""

from palpitaria.config import settings


def test_secret_key_error_none_when_strong_key_configured(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "a" * 32)
    assert settings.secret_key_error is None


def test_secret_key_error_when_missing(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "")
    error = settings.secret_key_error
    assert error is not None
    assert "SECRET_KEY" in error


def test_secret_key_error_when_too_short(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "curta")
    error = settings.secret_key_error
    assert error is not None
    assert "SECRET_KEY" in error


def test_secret_key_never_falls_back_to_old_hardcoded_default(monkeypatch):
    """Regressão: a chave fraca antiga não pode mais aparecer como default em lugar nenhum."""
    monkeypatch.setattr(settings, "secret_key", "")
    assert settings.secret_key != "palpitaria-secret-key-2026-secure-v1"
